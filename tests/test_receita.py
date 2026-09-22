"""Composição da receita: de onde vem o dinheiro antes de virar despesa.

A página do observatório já dizia *para onde vai* e não dizia *de onde vem* —
sobre municípios que, na sondagem de 21/09/2026, arrecadam em mediana **7,3%**
das próprias receitas correntes.

As duas armadilhas deste anexo não estouram: publicam número errado e ficam
quietas. Uma é a coluna (orçado × realizado), a outra é somar linha que já
está dentro de outra.
"""

import json
import unittest
from pathlib import Path

from fiscal.cli import classificar_receita
from fiscal.siconfi import (
    COD_RECEITAS_CORRENTES,
    COD_RECEITAS_DETALHE,
    COD_TOTAL,
    COLUNA_REALIZADA,
    RECEITAS_CORRENTES,
    RECEITAS_DETALHE,
    Receita,
    Resposta,
    receita,
    url_rreo_receita,
)

FIX = Path(__file__).parent / "fixtures"
CORPUS = json.loads((FIX / "rreo_a01_imperatriz.json").read_text(encoding="utf-8"))
CORPUS_INTRA = json.loads(
    (FIX / "rreo_a01_mata_grande.json").read_text(encoding="utf-8"))


def transporte_fixo(corpo: dict):
    def t(url: str) -> Resposta:
        return Resposta(200, json.dumps(corpo))
    return t


def ler():
    return receita(2105302, 2024, 6, transporte_fixo(CORPUS["resposta"]),
                   dormir=lambda _: None)


class TestAColunaEOrealizadoNaoOprevisto(unittest.TestCase):
    """A armadilha que publicaria **intenção como fato**.

    O anexo traz `PREVISÃO ATUALIZADA (a)` ao lado de `Até o Bimestre (c)`.
    A primeira é orçamento — o que a prefeitura *planejou* arrecadar; a segunda
    é o que ela arrecadou. Em Imperatriz elas diferem em 3,2%, o bastante para
    ninguém desconfiar olhando e o bastante para estar errado.
    """

    def test_le_o_realizado(self):
        r = ler()
        self.assertEqual(r.total, CORPUS["_receitas_correntes"])

    def test_e_NAO_le_a_previsao(self):
        r = ler()
        self.assertNotEqual(r.total, CORPUS["_previsao_atualizada"])

    def test_as_duas_colunas_diferem_de_verdade(self):
        # Deixa registrado que o teste acima não passa por acaso: se um dia a
        # fixture tiver previsão igual ao realizado, ele deixaria de provar.
        self.assertNotEqual(CORPUS["_receitas_correntes"],
                            CORPUS["_previsao_atualizada"])

    def test_a_constante_nomeia_a_coluna_certa(self):
        self.assertEqual(COLUNA_REALIZADA, "Até o Bimestre (c)")


class TestASomaFechaComOTotal(unittest.TestCase):
    """A integridade que a fonte oferece de graça, igual à do Anexo 02.

    Medido em 21/09/2026 sobre 7 municípios, de São Paulo (R$ 97,5 bi) a
    Itarumã/GO (R$ 56 mi): a maior diferença foi 2,1e-16 — epsilon de ponto
    flutuante, não divergência.
    """

    def test_fecha(self):
        self.assertIs(ler().fecha, True)

    def test_sem_total_a_resposta_e_desconhecido(self):
        # Ausência de régua não é aprovação — mesma regra do Anexo 02.
        r = Receita(1, 2024, 6, total=None,
                    valores={"TRANSFERÊNCIAS CORRENTES": 10.0})
        self.assertIsNone(r.fecha)

    def test_componente_faltando_quebra_o_fechamento(self):
        r = ler()
        sem = {c: v for c, v in r.valores.items()
               if c != "TRANSFERÊNCIAS CORRENTES"}
        quebrado = Receita(r.codigo_ibge, r.exercicio, r.periodo, r.total, sem)
        self.assertIs(quebrado.fecha, False)


class TestODetalheNaoEntraNaSoma(unittest.TestCase):
    """A segunda armadilha: contar o mesmo dinheiro duas vezes.

    "Impostos" e "Taxas" estão DENTRO de "IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE
    MELHORIA"; as duas linhas de transferência estão dentro de "TRANSFERÊNCIAS
    CORRENTES". Somar tudo o que a tabela guarda infla a receita — e o número
    continua bem formado, que é o que torna o defeito perigoso.
    """

    def test_o_detalhe_e_lido_e_guardado(self):
        r = ler()
        for c in RECEITAS_DETALHE:
            self.assertIn(c, r.valores, f"{c} deveria ser lido")

    def test_mas_NAO_entra_na_soma(self):
        r = ler()
        self.assertIs(r.fecha, True)  # fecha COM o detalhe guardado

    def test_somar_tudo_quebraria(self):
        # O contrafactual, para o teste acima não passar por acaso: somando
        # todas as linhas guardadas, a conta estoura.
        r = ler()
        tudo = sum(r.valores.values())
        self.assertGreater(tudo, r.total * 1.3)

    def test_as_duas_listas_nao_se_cruzam(self):
        self.assertFalse(set(RECEITAS_CORRENTES) & set(RECEITAS_DETALHE))


class TestOQueOAnexoResponde(unittest.TestCase):
    """O número que motivou coletar isto."""

    def test_propria_e_transferencia_saem_da_mesma_resposta(self):
        r = ler()
        propria = r.valores["IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE MELHORIA"]
        transf = r.valores["TRANSFERÊNCIAS CORRENTES"]
        self.assertEqual(propria, CORPUS["_impostos_taxas"])
        self.assertEqual(transf, CORPUS["_transferencias"])
        # Imperatriz arrecada 16,5% e recebe 78,6%. O município mediano da
        # sondagem arrecada 7,3%.
        self.assertLess(propria / r.total, 0.20)
        self.assertGreater(transf / r.total, 0.75)

    def test_a_transferencia_se_decompoe_por_origem(self):
        # "De onde vem a transferência" é a pergunta seguinte imediata.
        r = ler()
        self.assertEqual(r.valores["Transferências da União e de suas Entidades"],
                         CORPUS["_transf_uniao"])


class TestAusenciaEBorda(unittest.TestCase):
    def test_sem_itens_devolve_None(self):
        # "Não publicou" tem de ser distinguível de "publicou zero".
        r = receita(1, 2024, 6, transporte_fixo({"items": []}),
                    dormir=lambda _: None)
        self.assertIsNone(r)

    def test_bimestre_fora_da_faixa_levanta(self):
        # O RREO é bimestral (1 a 6); o RGF é quadrimestral (1 a 3). Confundir
        # os dois devolve vazio sem dizer por quê — por isso falha aqui.
        with self.assertRaises(ValueError):
            url_rreo_receita(2024, 7, 2105302)

    def test_a_url_pede_o_anexo_01(self):
        u = url_rreo_receita(2024, 6, 2105302)
        self.assertIn("RREO-Anexo+01", u)
        self.assertIn("nr_periodo=6", u)


class TestOHomonimoQueARegraDeFechamentoNAOVE(unittest.TestCase):
    """A armadilha mais silenciosa das três, e ela está em Imperatriz.

    `Transferências da União e de suas Entidades` é o nome de **duas** linhas
    da mesma resposta — uma dentro de TRANSFERÊNCIAS CORRENTES, outra dentro de
    RECEITAS DE CAPITAL. Só o `cod_conta` as separa:

        TransferenciasCorrentesDaUniaoEDeSuasEntidades   565.546.785,73
        TransferenciasDeCapitalDaUniaoEDeSuasEntidades     3.563.690,42

    **Isto existe em todos os entes**, e não só nos 0,5% com bloco intra. E a
    régua de fechamento não o alcança, porque o detalhe não entra na soma:
    importar o número de capital daria um valor bem formado, uma conta que
    fecha e uma frase errada na página.

    Os dezesseis primeiros testes desta fonte passaram sobre esta fixture sem
    ver isso — o leitor pegava a primeira ocorrência do nome, e a ordem da
    resposta o salvava. *Acertar por ordem não é identificar a linha.*

    **Declarado de propósito: estes quatro testes NÃO reprovam o leitor
    antigo** — ele acertava aqui, por sorte de ordenação. São guarda de
    regressão, não canário. Quem reprova o leitor antigo é
    `test_o_leitor_casa_por_cod_conta`, logo abaixo, e os dois testes do bloco
    intra. Confundir as duas coisas é confiar numa medição que não mede.
    """

    def test_le_a_transferencia_CORRENTE(self):
        self.assertEqual(
            ler().valores["Transferências da União e de suas Entidades"],
            CORPUS["_transf_uniao"])

    def test_e_NAO_a_de_capital(self):
        self.assertNotEqual(
            ler().valores["Transferências da União e de suas Entidades"],
            CORPUS["_transf_uniao_DE_CAPITAL"])

    def test_as_duas_linhas_existem_MESMO_na_fixture_que_passou(self):
        # Para o teste acima não passar por acaso: se um dia a fixture perder a
        # linha de capital, ele deixa de provar o que diz provar.
        homonimas = [x for x in CORPUS["resposta"]["items"]
                     if x["coluna"] == COLUNA_REALIZADA
                     and x["conta"].strip()
                     == "Transferências da União e de suas Entidades"]
        self.assertEqual(len(homonimas), 2)
        self.assertEqual({x["cod_conta"] for x in homonimas},
                         {"TransferenciasCorrentesDaUniaoEDeSuasEntidades",
                          "TransferenciasDeCapitalDaUniaoEDeSuasEntidades"})

    def test_a_regua_de_fechamento_e_CEGA_a_este_defeito(self):
        # O que justifica um teste próprio: trocar o detalhe pelo número errado
        # mantém `fecha` em True. Nenhuma verificação de integridade acusaria.
        r = ler()
        trocado = dict(r.valores)
        trocado["Transferências da União e de suas Entidades"] = \
            CORPUS["_transf_uniao_DE_CAPITAL"]
        errado = Receita(r.codigo_ibge, r.exercicio, r.periodo, r.total,
                         trocado)
        self.assertIs(errado.fecha, True)


class TestOCodigoEOquediscrimina(unittest.TestCase):
    """Fecha a porta do nome, para ninguém reabri-la por engano."""

    def test_o_leitor_casa_por_cod_conta(self):
        # Um item com o `conta` certo e `cod_conta` de outra linha não pode ser
        # lido — é exatamente a forma dos dois defeitos de 21/09/2026.
        disfarce = {"items": [{
            "coluna": COLUNA_REALIZADA,
            "cod_conta": "ReceitasCorrentesIntra",
            "conta": "RECEITAS CORRENTES",
            "valor": 999.0,
        }]}
        r = receita(1, 2024, 6, transporte_fixo(disfarce),
                    dormir=lambda _: None)
        self.assertIsNone(r)

    def test_nenhum_codigo_se_repete_entre_as_duas_tabelas(self):
        self.assertFalse(set(COD_RECEITAS_CORRENTES) & set(COD_RECEITAS_DETALHE))
        self.assertNotIn(COD_TOTAL, COD_RECEITAS_CORRENTES)

    def test_os_nomes_publicos_saem_das_tabelas(self):
        # `RECEITAS_CORRENTES` é o que o banco e a página falam; ele não pode
        # divergir da tabela de códigos que o leitor usa.
        self.assertEqual(RECEITAS_CORRENTES, tuple(COD_RECEITAS_CORRENTES.values()))
        self.assertEqual(RECEITAS_DETALHE, tuple(COD_RECEITAS_DETALHE.values()))


class TestOBlocoIntraOrcamentarioNaoPodeVazar(unittest.TestCase):
    """A terceira armadilha, achada pela varredura real em 21/09/2026.

    O anexo tem DOIS blocos com os mesmos nomes de conta:

        RECEITAS (EXCETO INTRA-ORÇAMENTÁRIAS) (I)   <- o que se quer
          RECEITAS CORRENTES, IMPOSTOS..., TRANSFERÊNCIAS...
        RECEITAS (INTRA-ORÇAMENTÁRIAS) (II)         <- a fronteira
          RECEITAS CORRENTES, ... de novo

    **E aqui o `rotulo` NÃO separa os dois** — é `Padrão` em ambos, ao
    contrário do Anexo 02, onde `Total das Despesas Exceto Intra-Orçamentárias`
    faz esse trabalho. O que separa é a POSIÇÃO: tudo depois do marcador
    `ReceitasIntraOrcamentariasTotal` pertence ao segundo bloco.

    Mata Grande/AL **não tem** RECEITA DE SERVIÇOS no primeiro bloco e tem
    R$ 9.698.837,21 no segundo. Um leitor que varra a resposta inteira importa
    aquele número, e a soma passa do total em 6,1%.

    A sondagem não pegou porque os 7 municípios medidos não tinham bloco intra
    — *amostra de sete não cobre uma estrutura que aparece em 0,5% dos casos*.
    Quem pegou foi a régua de fechamento, na varredura das 5.570.
    """

    def ler_intra(self):
        return receita(2705002, 2024, 6,
                       transporte_fixo(CORPUS_INTRA["resposta"]),
                       dormir=lambda _: None)

    def test_o_total_e_o_do_bloco_EXCETO_intra(self):
        r = self.ler_intra()
        self.assertEqual(r.total,
                         CORPUS_INTRA["_receitas_correntes_exceto_intra"])

    def test_e_NAO_o_do_bloco_intra(self):
        r = self.ler_intra()
        self.assertNotEqual(r.total, CORPUS_INTRA["_receitas_correntes_INTRA"])

    def test_componente_que_SO_existe_no_intra_nao_e_lida(self):
        # É o caso exato: sem RECEITA DE SERVIÇOS no primeiro bloco, o valor
        # do segundo não pode aparecer.
        r = self.ler_intra()
        self.assertNotIn("RECEITA DE SERVIÇOS", r.valores)

    def test_e_por_isso_a_soma_FECHA(self):
        # A prova de que a correção é a certa: com o bloco intra fora, a
        # régua da própria fonte aprova.
        self.assertIs(self.ler_intra().fecha, True)


class TestDeclaracaoIncompletaQueAReguaAprova(unittest.TestCase):
    """A quarta armadilha, e a única que a fonte não pode denunciar sozinha.

    Apiaí/SP declarou R$ 5,06 mi de receita corrente sem nenhuma transferência,
    e a soma das componentes FECHAVA com o total declarado. O site publicou
    *"79,0% de impostos"* sobre um município que recebe FPM como todos.

    A régua de fechamento não pode ver isso por construção: ela compara a
    declaração consigo mesma. Quem vê é uma segunda fonte (a RCL do RGF, que
    dizia R$ 134 mi) ou um fato legal (o FPM é constitucional).
    """

    def test_sem_transferencia_e_incompleta_mesmo_fechando(self):
        # O caso real, com os números reais: a declaração fecha e é falsa.
        r = Receita(3502705, 2024, 6, 5062526.0, {
            "IMPOSTOS, TAXAS E CONTRIBUIÇÕES DE MELHORIA": 4001229.0,
            "CONTRIBUIÇÕES": 1061297.0})
        self.assertIs(r.fecha, True, "a régua da fonte APROVA")
        self.assertEqual(classificar_receita(r.total, None, 134180280.0),
                         "sem transferência corrente")

    def test_transferencia_zero_tambem(self):
        self.assertEqual(classificar_receita(100.0, 0.0, 100.0),
                         "sem transferência corrente")

    def test_sem_transferencia_vence_a_razao(self):
        # É certeza, não suspeita: o motivo certo tem de aparecer mesmo quando
        # a razão também estaria fora da faixa.
        self.assertEqual(classificar_receita(5.0, None, 134.0),
                         "sem transferência corrente")

    def test_razao_fora_da_faixa_nomeia_o_lado(self):
        self.assertEqual(classificar_receita(40.0, 30.0, 100.0),
                         "receita muito abaixo da RCL")
        self.assertEqual(classificar_receita(300.0, 200.0, 100.0),
                         "RCL muito abaixo da receita")

    def test_municipio_normal_passa(self):
        # Mediana medida: razão 1,000. Imperatriz, da fixture, dentro da faixa.
        self.assertIsNone(classificar_receita(
            CORPUS["_receitas_correntes"], CORPUS["_transferencias"],
            CORPUS["_receitas_correntes"] * 0.98))

    def test_sem_rcl_nao_acusa(self):
        # Sem a segunda fonte, a razão não existe — ausência de régua não é
        # reprovação, pela mesma regra do `fecha` sem total.
        self.assertIsNone(classificar_receita(100.0, 80.0, None))


if __name__ == "__main__":
    unittest.main()

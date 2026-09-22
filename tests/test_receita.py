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

from fiscal.siconfi import (
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


if __name__ == "__main__":
    unittest.main()

"""O armazém: idempotência, retomada, e a diferença entre ausência e zero."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fiscal.armazem import (
    _apagar_fatia, abrir, abrir_coleta, fechar_coleta, gravar_entes,
    gravar_funcoes, gravar_pessoal, gravar_receita, ja_coletados,
)
from fiscal.siconfi import Ente, Funcoes, Pessoal, Receita

SALVADOR = Ente(2927408, "Salvador", "BA", "NE", "M", 2610987, "13927801000149")
JOAO_PESSOA = Ente(2507507, "João Pessoa", "PB", "NE", "M", 833932, None)


class BancoTemporario(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.caminho = Path(self.dir.name) / "teste.db"

    def tearDown(self):
        self.dir.cleanup()


class TestIdempotencia(BancoTemporario):
    """Critério de aceite 1: rodar duas vezes não duplica linha nem muda número."""

    def test_ingerir_o_mesmo_ente_duas_vezes_da_uma_linha(self):
        for _ in range(2):
            with abrir(self.caminho) as con:
                gravar_entes(con, [SALVADOR, JOAO_PESSOA])
        with abrir(self.caminho) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ente").fetchone()[0], 2)

    def test_ingerir_o_mesmo_periodo_duas_vezes_da_uma_linha(self):
        p = Pessoal(2927408, 2024, 3, 1e10, 9.9e9, 3.3e9, 32.37, 51.3)
        for _ in range(2):
            with abrir(self.caminho) as con:
                gravar_pessoal(con, 2927408, 2024, 3, p)
        with abrir(self.caminho) as con:
            linhas = con.execute("SELECT * FROM pessoal").fetchall()
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["percentual"], 32.37)

    def test_periodos_diferentes_do_mesmo_ente_convivem(self):
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 2927408, 2024, 2, Pessoal(2927408, 2024, 2, None, None, None, 30.0, 51.3))
            gravar_pessoal(con, 2927408, 2024, 3, Pessoal(2927408, 2024, 3, None, None, None, 32.37, 51.3))
        with abrir(self.caminho) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM pessoal").fetchone()[0], 2)


class TestAusenciaNaoEZero(BancoTemporario):
    """Critério de aceite 8, e o mais fácil de errar.

    Três estados que nunca podem virar o mesmo: **não perguntei**, **perguntei e
    ele não publicou**, e **publicou zero**. Zero é um número.
    """

    def test_nao_publicou_e_gravado_com_publicou_zero_e_campos_nulos(self):
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 2927408, 2024, 3, None)
        with abrir(self.caminho) as con:
            r = con.execute("SELECT * FROM pessoal").fetchone()
        self.assertEqual(r["publicou"], 0)
        self.assertIsNone(r["percentual"])

    def test_publicou_zero_e_diferente_de_nao_publicou(self):
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 1, 2024, 3, Pessoal(1, 2024, 3, 0.0, 0.0, 0.0, 0.0, 51.3))
            gravar_pessoal(con, 2, 2024, 3, None)
        with abrir(self.caminho) as con:
            zero = con.execute("SELECT * FROM pessoal WHERE codigo_ibge=1").fetchone()
            ausente = con.execute("SELECT * FROM pessoal WHERE codigo_ibge=2").fetchone()
        self.assertEqual((zero["publicou"], zero["percentual"]), (1, 0.0))
        self.assertEqual((ausente["publicou"], ausente["percentual"]), (0, None))

    def test_nao_perguntei_nao_tem_linha_nenhuma(self):
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 1, 2024, 3, None)
        with abrir(self.caminho) as con:
            self.assertIsNone(
                con.execute("SELECT * FROM pessoal WHERE codigo_ibge=99").fetchone())


class TestProveniencia(BancoTemporario):
    """Critério de aceite 5: toda observação carrega fonte e data de coleta."""

    def test_fonte_e_data_gravadas_inclusive_na_ausencia(self):
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 1, 2024, 3, None)
        with abrir(self.caminho) as con:
            r = con.execute("SELECT * FROM pessoal").fetchone()
        self.assertIn("SICONFI", r["fonte"])
        self.assertTrue(r["coletado_em"].startswith("20"))


class TestRetomada(BancoTemporario):
    """Critério de aceite 2: interrompida na metade, continua de onde parou.

    Uma varredura do Nordeste leva ~57 minutos. Reler o que já veio é o
    desperdício que importa.
    """

    def test_ja_coletados_inclui_quem_nao_publicou(self):
        # Se a ausência não contasse como "já perguntei", a retomada perguntaria
        # de novo a todo município que não publica -- para sempre.
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 1, 2024, 3, Pessoal(1, 2024, 3, None, None, None, 30.0, 51.3))
            gravar_pessoal(con, 2, 2024, 3, None)
        with abrir(self.caminho) as con:
            self.assertEqual(ja_coletados(con, 2024, 3), {1, 2})

    def test_ja_coletados_nao_mistura_periodos(self):
        with abrir(self.caminho) as con:
            gravar_pessoal(con, 1, 2024, 2, None)
        with abrir(self.caminho) as con:
            self.assertEqual(ja_coletados(con, 2024, 3), set())


class TestRegistroDaColeta(BancoTemporario):
    """A coleta que falhou também fica registrada -- execução sem rastro é
    execução que ninguém consegue depurar depois."""

    def test_coleta_interrompida_guarda_a_causa(self):
        with abrir(self.caminho) as con:
            rid = abrir_coleta(con, 2024, 3)
            fechar_coleta(con, rid, lidos=812, publicaram=790, falhou_com="ErroSiconfi: desisti")
        with abrir(self.caminho) as con:
            r = con.execute("SELECT * FROM coleta").fetchone()
        self.assertEqual(r["lidos"], 812)
        self.assertIn("desisti", r["falhou_com"])


class TestReleituraSubstituiAFatia(BancoTemporario):
    """UPSERT nunca apaga — e é por isso que releitura precisava de conserto.

    Achado em 21/09/2026: o leitor da receita importava uma linha do bloco
    intra-orçamentário. Consertado o leitor, a releitura **não produz** aquela
    linha — e, com gravação só de UPSERT, ela ficaria no banco enquanto
    `receita_consulta.fecha` passava a 1.

    Trava verde sobre dado sujo é a forma de defeito mais cara deste projeto.
    Estes testes cobram o contrário: o banco é igual à ÚLTIMA leitura.
    """

    def gravar(self, valores, total=None):
        r = Receita(2705002, 2024, 6, total, valores)
        with abrir(self.caminho) as con:
            gravar_receita(con, 2705002, 2024, 6, r)

    def contas(self):
        with abrir(self.caminho) as con:
            return {x[0] for x in con.execute(
                "SELECT conta FROM receita WHERE codigo_ibge=2705002")}

    def test_linha_que_sumiu_da_leitura_sai_do_banco(self):
        # É o caso Mata Grande, na forma mínima.
        self.gravar({"TRANSFERÊNCIAS CORRENTES": 10.0,
                     "RECEITA DE SERVIÇOS": 9.7}, total=19.7)
        self.assertIn("RECEITA DE SERVIÇOS", self.contas())
        self.gravar({"TRANSFERÊNCIAS CORRENTES": 10.0}, total=10.0)
        self.assertEqual(self.contas(), {"TRANSFERÊNCIAS CORRENTES"})

    def test_o_valor_das_que_ficaram_e_o_novo(self):
        self.gravar({"TRANSFERÊNCIAS CORRENTES": 10.0}, total=10.0)
        self.gravar({"TRANSFERÊNCIAS CORRENTES": 11.0}, total=11.0)
        with abrir(self.caminho) as con:
            r = con.execute("SELECT valor, total_declarado FROM receita").fetchone()
        self.assertEqual((r["valor"], r["total_declarado"]), (11.0, 11.0))

    def test_ente_que_deixou_de_publicar_fica_sem_linha_nenhuma(self):
        # Sem isto, `publicou=0` conviveria com valores gravados — estado que
        # nenhuma leitura do banco saberia interpretar.
        self.gravar({"TRANSFERÊNCIAS CORRENTES": 10.0}, total=10.0)
        with abrir(self.caminho) as con:
            gravar_receita(con, 2705002, 2024, 6, None)
        with abrir(self.caminho) as con:
            self.assertEqual(self.contas(), set())
            self.assertEqual(con.execute(
                "SELECT publicou FROM receita_consulta").fetchone()[0], 0)

    def test_a_fatia_apagada_e_SO_a_do_ente_e_do_periodo(self):
        # O apagamento é cirúrgico: errar o recorte aqui esvaziaria o banco a
        # cada varredura, e o sintoma apareceria só na contagem final.
        self.gravar({"TRANSFERÊNCIAS CORRENTES": 10.0}, total=10.0)
        with abrir(self.caminho) as con:
            gravar_receita(con, 3550308, 2024, 6,
                           Receita(3550308, 2024, 6, 5.0, {"CONTRIBUIÇÕES": 5.0}))
            gravar_receita(con, 2705002, 2024, 4,
                           Receita(2705002, 2024, 4, 7.0, {"CONTRIBUIÇÕES": 7.0}))
        self.gravar({"CONTRIBUIÇÕES": 1.0}, total=1.0)
        with abrir(self.caminho) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM receita").fetchone()[0], 3)

    def test_a_gemea_das_funcoes_tambem_substitui(self):
        # O defeito era idêntico nas duas gravações; corrigir uma só deixaria a
        # outra esperando a próxima correção de leitor.
        with abrir(self.caminho) as con:
            gravar_funcoes(con, 2705002, 2024, 6,
                           Funcoes(2705002, 2024, 6, 3.0,
                                   {"Saúde": 2.0, "Educação": 1.0}))
        with abrir(self.caminho) as con:
            gravar_funcoes(con, 2705002, 2024, 6,
                           Funcoes(2705002, 2024, 6, 2.0, {"Saúde": 2.0}))
        with abrir(self.caminho) as con:
            self.assertEqual({x[0] for x in con.execute(
                "SELECT funcao FROM despesa_funcao")}, {"Saúde"})

    def test_apagar_tabela_de_fora_do_contrato_levanta(self):
        # O nome da tabela é interpolado na SQL — o contrato fechado é o que
        # impede isso de virar porta de entrada.
        with abrir(self.caminho) as con:
            with self.assertRaises(ValueError):
                _apagar_fatia(con, "ente", 2705002, 2024, 6)


if __name__ == "__main__":
    unittest.main()

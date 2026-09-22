"""Persistência em SQLite. Nada sai da máquina.

O desenho responde a dois critérios de aceite da especificação: **ingerir duas
vezes não muda nada** e **ausência é gravada como ausência, nunca como zero**.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .siconfi import Ente, Pessoal

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ente (
    codigo_ibge INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL,
    uf          TEXT NOT NULL,
    regiao      TEXT NOT NULL,
    esfera      TEXT NOT NULL,
    populacao   INTEGER,
    cnpj        TEXT,
    visto_em    TEXT NOT NULL
);

-- Uma linha por ente/exercicio/periodo. `publicou` separa as duas ausencias
-- que nunca podem virar a mesma coisa:
--   publicou = 0  -> o ente NAO entregou o relatorio (items: [] com HTTP 200)
--   publicou = 1 com percentual NULL -> entregou, mas sem aquele campo
-- Zero em `percentual` continua significando zero de verdade.
CREATE TABLE IF NOT EXISTS pessoal (
    codigo_ibge       INTEGER NOT NULL,
    exercicio         INTEGER NOT NULL,
    periodo           INTEGER NOT NULL,
    publicou          INTEGER NOT NULL,
    rcl               REAL,
    rcl_ajustada      REAL,
    despesa           REAL,
    percentual        REAL,
    limite_prudencial REAL,
    fonte             TEXT NOT NULL,
    coletado_em       TEXT NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, periodo)
);

-- Despesa liquidada por funcao orcamentaria (RREO Anexo 02). Uma linha por
-- ente/exercicio/bimestre/funcao. `total_declarado` repete em todas as linhas
-- do mesmo relatorio de proposito: e a regua contra a qual a soma se confere,
-- e guarda-la junto evita depender de uma segunda consulta para verificar.
CREATE TABLE IF NOT EXISTS despesa_funcao (
    codigo_ibge     INTEGER NOT NULL,
    exercicio       INTEGER NOT NULL,
    periodo         INTEGER NOT NULL,
    funcao          TEXT    NOT NULL,
    valor           REAL,
    total_declarado REAL,
    fonte           TEXT NOT NULL,
    coletado_em     TEXT NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, periodo, funcao)
);

-- Quem foi consultado para funcoes, inclusive quem nao publicou. Sem isto a
-- retomada perguntaria de novo, para sempre, a todo municipio sem relatorio.
CREATE TABLE IF NOT EXISTS funcao_consulta (
    codigo_ibge INTEGER NOT NULL,
    exercicio   INTEGER NOT NULL,
    periodo     INTEGER NOT NULL,
    publicou    INTEGER NOT NULL,
    fecha       INTEGER,
    coletado_em TEXT NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, periodo)
);

-- Composicao da RECEITA corrente (RREO Anexo 01). Mesmo desenho da
-- `despesa_funcao`, e de proposito: sao os dois lados da mesma pergunta, e o
-- que ja esta provado nao se reinventa.
--
-- `total_declarado` repete em todas as linhas do mesmo relatorio pela mesma
-- razao de la: e a regua contra a qual a soma se confere, e guarda-la junto
-- evita uma segunda consulta para verificar.
--
-- **Atencao ao somar.** Nem toda linha entra na soma: as quatro de DETALHE
-- (Impostos, Taxas, Transferencias da Uniao, Transferencias dos Estados) estao
-- DENTRO das oito componentes. Somar tudo conta o mesmo dinheiro duas vezes.
-- Quem soma usa `siconfi.RECEITAS_CORRENTES`, que e a lista que fecha.
CREATE TABLE IF NOT EXISTS receita (
    codigo_ibge     INTEGER NOT NULL,
    exercicio       INTEGER NOT NULL,
    periodo         INTEGER NOT NULL,
    conta           TEXT    NOT NULL,
    valor           REAL,
    total_declarado REAL,
    fonte           TEXT NOT NULL,
    coletado_em     TEXT NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, periodo, conta)
);

-- Quem foi consultado para receita, inclusive quem nao publicou -- mesma razao
-- da `funcao_consulta`: sem isto a retomada pergunta de novo, para sempre, a
-- todo municipio sem relatorio.
CREATE TABLE IF NOT EXISTS receita_consulta (
    codigo_ibge INTEGER NOT NULL,
    exercicio   INTEGER NOT NULL,
    periodo     INTEGER NOT NULL,
    publicou    INTEGER NOT NULL,
    fecha       INTEGER,
    coletado_em TEXT NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, periodo)
);

-- Aplicacao em saude (SIOPS/DATASUS). Uma linha por municipio/exercicio/
-- indicador. **A ausencia nao tem linha**: no SIOPS uma requisicao traz a UF
-- inteira, entao ano sem valor e ausencia da FONTE, nao pergunta que faltou
-- fazer -- ao contrario do `pessoal`, onde `publicou` separa as duas.
CREATE TABLE IF NOT EXISTS saude (
    codigo_ibge INTEGER NOT NULL,
    exercicio   INTEGER NOT NULL,
    indicador   TEXT    NOT NULL,
    valor       REAL    NOT NULL,
    fonte       TEXT    NOT NULL,
    coletado_em TEXT    NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, indicador)
);

-- A COBERTURA de cada varredura de UF, guardada de proposito. Sem ela, uma
-- coleta que trouxesse metade dos municipios produziria um banco valido,
-- coerente e menor -- e nada acusaria. Ver a licao de 07/09/2026 em
-- `.claude/rules/stack.md`: artefato MENOR nao e artefato quebrado.
CREATE TABLE IF NOT EXISTS saude_varredura (
    uf          TEXT    NOT NULL,
    indicador   TEXT    NOT NULL,
    municipios  INTEGER NOT NULL,
    valores     INTEGER NOT NULL,
    nao_casaram INTEGER NOT NULL,
    nomes_fora  INTEGER NOT NULL,
    coletado_em TEXT    NOT NULL,
    PRIMARY KEY (uf, indicador)
);

-- Marca de progresso: e o que torna a varredura retomavel sem reler o que ja
-- veio. Uma hora de rede e tempo de sobra para algo dar errado.
CREATE TABLE IF NOT EXISTS coleta (
    iniciada_em  TEXT NOT NULL,
    terminada_em TEXT,
    exercicio    INTEGER NOT NULL,
    periodo      INTEGER NOT NULL,
    lidos        INTEGER NOT NULL DEFAULT 0,
    publicaram   INTEGER NOT NULL DEFAULT 0,
    falhou_com   TEXT
);
"""

FONTE = "SICONFI/Tesouro Nacional — RGF Anexo 01"
FONTE_FUNCOES = "SICONFI/Tesouro Nacional — RREO Anexo 02"
FONTE_RECEITA = "SICONFI/Tesouro Nacional — RREO Anexo 01"
FONTE_SAUDE = "SIOPS/Ministério da Saúde — TabNet/DATASUS"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def abrir(caminho: str | Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(ESQUEMA)
        yield con
        con.commit()
    finally:
        con.close()


def gravar_entes(con: sqlite3.Connection, entes: list[Ente]) -> int:
    """Idempotente: o mesmo ente gravado duas vezes continua sendo uma linha."""
    con.executemany(
        "INSERT INTO ente (codigo_ibge, nome, uf, regiao, esfera, populacao, cnpj, visto_em)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge) DO UPDATE SET"
        "   nome=excluded.nome, uf=excluded.uf, regiao=excluded.regiao,"
        "   esfera=excluded.esfera, populacao=excluded.populacao,"
        "   cnpj=excluded.cnpj, visto_em=excluded.visto_em",
        [(e.codigo_ibge, e.nome, e.uf, e.regiao, e.esfera, e.populacao, e.cnpj, agora())
         for e in entes],
    )
    return len(entes)


def gravar_pessoal(
    con: sqlite3.Connection,
    codigo_ibge: int,
    exercicio: int,
    periodo: int,
    p: Pessoal | None,
) -> None:
    """Grava o resultado -- inclusive quando o resultado é "não publicou".

    `p is None` não é motivo para não gravar: gravar a ausência é o que impede a
    varredura de tentar o mesmo ente de novo a cada retomada, e é o que permite
    distinguir "não entregou" de "ainda não perguntei".
    """
    con.execute(
        "INSERT INTO pessoal (codigo_ibge, exercicio, periodo, publicou, rcl,"
        " rcl_ajustada, despesa, percentual, limite_prudencial, fonte, coletado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge, exercicio, periodo) DO UPDATE SET"
        "   publicou=excluded.publicou, rcl=excluded.rcl,"
        "   rcl_ajustada=excluded.rcl_ajustada, despesa=excluded.despesa,"
        "   percentual=excluded.percentual, limite_prudencial=excluded.limite_prudencial,"
        "   fonte=excluded.fonte, coletado_em=excluded.coletado_em",
        (codigo_ibge, exercicio, periodo, 1 if p else 0,
         p.rcl if p else None, p.rcl_ajustada if p else None,
         p.despesa if p else None,
         p.percentual if p else None, p.limite_prudencial if p else None,
         FONTE, agora()),
    )


def ja_coletados(con: sqlite3.Connection, exercicio: int, periodo: int) -> set[int]:
    """Quem já foi perguntado neste período -- a base da retomada."""
    return {r[0] for r in con.execute(
        "SELECT codigo_ibge FROM pessoal WHERE exercicio=? AND periodo=?",
        (exercicio, periodo))}


def abrir_coleta(con: sqlite3.Connection, exercicio: int, periodo: int) -> int:
    cur = con.execute(
        "INSERT INTO coleta (iniciada_em, exercicio, periodo) VALUES (?,?,?)",
        (agora(), exercicio, periodo))
    return cur.lastrowid


def fechar_coleta(con: sqlite3.Connection, rowid: int, lidos: int,
                  publicaram: int, falhou_com: str | None = None) -> None:
    con.execute(
        "UPDATE coleta SET terminada_em=?, lidos=?, publicaram=?, falhou_com=?"
        " WHERE rowid=?",
        (agora(), lidos, publicaram, falhou_com, rowid))


#: Tabelas cuja fatia por ente é substituída inteira a cada releitura.
TABELAS_POR_FATIA = ("despesa_funcao", "receita")


def _apagar_fatia(con: sqlite3.Connection, tabela: str, codigo_ibge: int,
                  exercicio: int, periodo: int) -> int:
    """Apaga a fatia do ente antes de regravá-la. **Releitura substitui.**

    Achado em 21/09/2026, ao consertar o leitor da receita. As duas gravações
    eram UPSERT puro, e UPSERT **nunca apaga**: uma linha que o leitor corrigido
    deixou de produzir sobreviveria à releitura.

    O caso concreto era Mata Grande/AL, com um `RECEITA DE SERVIÇOS` importado
    do bloco intra-orçamentário. Releitura com o leitor certo não produz aquela
    linha -- e, sem este apagamento, ela ficaria no banco enquanto o
    `receita_consulta.fecha` passava a **1**. Trava verde, dado sujo: a forma
    exata do defeito que este projeto já pagou três vezes.

    **É também por isso que o apagamento vem ANTES do `if not r: return`**: um
    ente que publicou e deixou de publicar tem de ficar sem linha nenhuma,
    senão `publicou=0` conviveria com valores gravados.

    O preço, dito de propósito: se a fonte devolver vazio por instabilidade, a
    fatia daquele ente se perde até a próxima varredura. É o lado certo do
    prejuízo -- fatia faltando se recoleta e se enxerga na contagem; fatia
    fantasma não se enxerga de jeito nenhum.
    """
    if tabela not in TABELAS_POR_FATIA:  # nunca interpolar nome vindo de fora
        raise ValueError(f"tabela fora do contrato de fatia: {tabela!r}")
    cur = con.execute(
        f"DELETE FROM {tabela} WHERE codigo_ibge=? AND exercicio=? AND periodo=?",
        (codigo_ibge, exercicio, periodo))
    return cur.rowcount


def gravar_funcoes(con: sqlite3.Connection, codigo_ibge: int, exercicio: int,
                   periodo: int, f) -> None:
    """Grava a despesa por função -- e grava também quando não houve nenhuma.

    `f is None` significa "consultado, não publicou". Sem registrar isso, a
    retomada perguntaria de novo a cada execução a todo município sem relatório.

    **Substitui a fatia do ente, não faz merge** -- ver `_apagar_fatia`.
    """
    agora_ = agora()
    con.execute(
        "INSERT INTO funcao_consulta (codigo_ibge, exercicio, periodo, publicou,"
        " fecha, coletado_em) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge, exercicio, periodo) DO UPDATE SET"
        "   publicou=excluded.publicou, fecha=excluded.fecha,"
        "   coletado_em=excluded.coletado_em",
        (codigo_ibge, exercicio, periodo, 1 if f else 0,
         None if (f is None or f.fecha is None) else int(f.fecha), agora_))
    _apagar_fatia(con, "despesa_funcao", codigo_ibge, exercicio, periodo)
    if not f:
        return
    con.executemany(
        "INSERT INTO despesa_funcao (codigo_ibge, exercicio, periodo, funcao,"
        " valor, total_declarado, fonte, coletado_em) VALUES (?,?,?,?,?,?,?,?)",
        [(codigo_ibge, exercicio, periodo, nome, valor, f.total,
          FONTE_FUNCOES, agora_) for nome, valor in f.valores.items()])


def gravar_receita(con: sqlite3.Connection, codigo_ibge: int, exercicio: int,
                   periodo: int, r) -> None:
    """Grava a composição da receita -- e grava também quando não houve nenhuma.

    Gêmea de `gravar_funcoes`, e deliberadamente: são os dois lados da mesma
    pergunta, vindos do mesmo relatório, com a mesma cobertura (medido em
    21/09/2026: 20 de 20 entre quem publicou o Anexo 02, 0 de 20 entre quem
    não publicou).

    `r is None` significa "consultado, não publicou" -- sem registrar isso, a
    retomada perguntaria de novo a cada execução a todo município sem relatório.

    **Substitui a fatia do ente, não faz merge** -- ver `_apagar_fatia`.
    """
    agora_ = agora()
    con.execute(
        "INSERT INTO receita_consulta (codigo_ibge, exercicio, periodo, publicou,"
        " fecha, coletado_em) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge, exercicio, periodo) DO UPDATE SET"
        "   publicou=excluded.publicou, fecha=excluded.fecha,"
        "   coletado_em=excluded.coletado_em",
        (codigo_ibge, exercicio, periodo, 1 if r else 0,
         None if (r is None or r.fecha is None) else int(r.fecha), agora_))
    _apagar_fatia(con, "receita", codigo_ibge, exercicio, periodo)
    if not r:
        return
    con.executemany(
        "INSERT INTO receita (codigo_ibge, exercicio, periodo, conta,"
        " valor, total_declarado, fonte, coletado_em) VALUES (?,?,?,?,?,?,?,?)",
        [(codigo_ibge, exercicio, periodo, conta, valor, r.total,
          FONTE_RECEITA, agora_) for conta, valor in r.valores.items()])


def gravar_saude(con: sqlite3.Connection, uf: str, indicador: str,
                 series, por6: dict, nomes_fora: int) -> dict:
    """Grava a série de saúde de uma UF, e devolve o que foi gravado.

    `por6` vem de `siops.resolver_ibge` e traduz o código de seis dígitos do
    SIOPS para os sete do IBGE. **Município que não casa não é gravado e não é
    silenciado**: ele volta contado em `nao_casaram`, para quem chamou decidir.
    Casar 99% e seguir em frente é como uma base perde um estado inteiro sem que
    nada acuse.
    """
    agora_ = agora()
    linhas = []
    nao_casaram = []
    for s in series:
        ibge = por6.get(s.codigo_siops)
        if ibge is None:
            nao_casaram.append((s.codigo_siops, s.nome))
            continue
        for ano, valor in s.valores.items():
            linhas.append((ibge, ano, indicador, valor, FONTE_SAUDE, agora_))
    con.executemany(
        "INSERT INTO saude (codigo_ibge, exercicio, indicador, valor, fonte,"
        " coletado_em) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge, exercicio, indicador) DO UPDATE SET"
        "   valor=excluded.valor, fonte=excluded.fonte,"
        "   coletado_em=excluded.coletado_em",
        linhas)
    con.execute(
        "INSERT INTO saude_varredura (uf, indicador, municipios, valores,"
        " nao_casaram, nomes_fora, coletado_em) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(uf, indicador) DO UPDATE SET"
        "   municipios=excluded.municipios, valores=excluded.valores,"
        "   nao_casaram=excluded.nao_casaram, nomes_fora=excluded.nomes_fora,"
        "   coletado_em=excluded.coletado_em",
        (uf.upper(), indicador, len(series) - len(nao_casaram), len(linhas),
         len(nao_casaram), nomes_fora, agora_))
    return {"municipios": len(series) - len(nao_casaram), "valores": len(linhas),
            "nao_casaram": nao_casaram}


def cobertura_saude(con: sqlite3.Connection, indicador: str) -> dict:
    """Quantos municípios e valores já existem, por UF -- a régua do encolher."""
    return {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT uf, municipios, valores FROM saude_varredura WHERE indicador=?",
        (indicador,))}


def ja_consultados_funcoes(con: sqlite3.Connection, exercicio: int,
                           periodo: int) -> set[int]:
    return {r[0] for r in con.execute(
        "SELECT codigo_ibge FROM funcao_consulta WHERE exercicio=? AND periodo=?",
        (exercicio, periodo))}


def ja_consultados_receita(con: sqlite3.Connection, exercicio: int,
                           periodo: int) -> set[int]:
    return {r[0] for r in con.execute(
        "SELECT codigo_ibge FROM receita_consulta WHERE exercicio=? AND periodo=?",
        (exercicio, periodo))}

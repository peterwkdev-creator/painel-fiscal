"""Ponto de entrada: `python -m fiscal`.

## As duas linhas de `reconfigure`, e por que elas moram aqui

No Windows o `stdout` abre em **cp1252** sempre que a saída vai para um cano —
toda varredura em segundo plano, todo `| tee`, o `atualizar-painel.py` que
captura a saída deste CLI. Um caractere fora daquela tabela levanta
`UnicodeEncodeError` e mata o processo **na hora de contar o que fez** — o
erro nº 10 do `.claude/rules/medir.md`, que já custou quatro diagnósticos.

O `observatorio` ganhou estas linhas em 15/09/2026; este CLI, não. Achado numa
revisão em 22/09/2026, lendo os bytes de uma saída redirecionada: `í` saía como
`0xED`, cp1252 puro. **Ele funcionava por acaso** — todo caractere que imprime
(`í`, `ã`, `–`, `·`) existe naquela tabela. O primeiro `─` ou `→` num `print`
novo derrubaria uma varredura de três horas no fim, depois de gravada.

Aqui e não em `cli.py`: quem importa o módulo como biblioteca — os testes, o
`atualizar-painel.py` — não deve ter o `stdout` do processo reconfigurado por
baixo.
"""

import sys

from .cli import principal

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.exit(principal())

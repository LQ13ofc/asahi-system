# Graphify para desenvolvimento

Graphify é uma ferramenta opcional de navegação de código. O projeto fixa o repositório oficial em [`tools/graphify.lock.json`](../tools/graphify.lock.json), no commit `1d1e03b4c88abf94a3f450c838e3c744ae514ef9` (versão `0.9.84`, licença Apache-2.0).

O helper `scripts/graphify_project.py` executa `graphify extract --code-only --no-cluster` por meio de `uvx`. A extração AST roda localmente, sem LLM, API ou serviço residente. `graphify-out/` é gerado localmente e ignorado pelo Git. O modo `both` cria um grafo separado por repositório; não combina nem copia código do Quickshell.

```bash
python3 scripts/graphify_project.py --dry-run
python3 scripts/graphify_project.py asahi
python3 scripts/graphify_project.py both --quickshell-dir ../quickshell-
```

Para worktrees em diretórios diferentes, passe o caminho real do checkout `quickshell-`. O helper verifica o `origin` local dos dois repos e falha se apontar para outra origem. `niri+` e o runtime do desktop não dependem de Graphify. A ausência de `uvx` ou de rede só impede a geração do grafo; não afeta build, testes nem uso do sistema.

Os `.graphifyignore` excluem baseline do M1, capturas de experimentos, saídas geradas e padrões comuns de credenciais. O grafo continua sendo derivado e não substitui a leitura do código-fonte.

Smoke test Cloud do candidato de auditoria: após excluir explicitamente o submodule `external/quickshell/`, o grafo de `asahi-system` contém 890 nós e 2.439 relações de 42 arquivos-fonte. O checkout visual não é analisado dentro do grafo de sistema; cada repositório gera seu próprio grafo. Arquivos QML, KDL, systemd e outros sem extensão suportada continuam exigindo auditoria e testes específicos.

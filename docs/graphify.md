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

Smoke test no Cloud do commit atual: `asahi-system` produziu 645 nós e 1.833 relações a partir de 35 arquivos reconhecidos. Arquivos de configuração do Niri/systemd/KDL não são cobertos pelo extrator atual, então esses caminhos continuam exigindo leitura direta dos arquivos.

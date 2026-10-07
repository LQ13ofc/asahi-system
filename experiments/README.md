# Experimentos

Cada experimento compara uma única mudança e segue:

`BASELINE → HIPÓTESE → UMA MUDANÇA → BENCHMARK A/B → COMPARAÇÃO → KEEP / REVERT / INCONCLUSIVE → DOCUMENTAÇÃO → COMMIT → PR`.

Copie `template/` para uma pasta curta com nome descritivo. Preencha `hypothesis.md` antes da mudança. Guarde capturas nos diretórios `before/` e `after/`; não sobrescreva baselines anteriores. Registre contexto e comparação em `conclusion.md`; `result.json` é um resumo legível por máquina. Só use `KEEP`, `REVERT` ou `INCONCLUSIVE`. `UNKNOWN` em classificações não significa removível.

No M1, capture cinco execuções idle seguindo [`../docs/benchmark-protocol.md`](../docs/benchmark-protocol.md). O coletor não aplica alterações. A aprovação de uma hipótese não autoriza mudanças fora do escopo do experimento.

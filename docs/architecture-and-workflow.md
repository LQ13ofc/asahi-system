# Arquitetura e workflow

## Limites entre repositórios

| Repositório | Responsabilidade | Estado observado |
|---|---|---|
| [`asahi-system`](https://github.com/LQ13ofc/asahi-system) | Fonte principal do sistema Fedora Asahi: perfil do hardware, baseline, CLI Niri+, configuração Niri, lifecycle e integração. | Baseline e coletor read-only estão em main. A sessão Niri+Quickshell, o lifecycle e as correções do release gate estão no PR #8 aberto, sem merge. O primeiro login real no M1 confirmou compositor, entrada, terminal e barra; o race Wayland e vazamentos KDE/PIM ficaram confirmados em logs e aguardam reteste após o PR. |
| [`quickshell-`](https://github.com/LQ13ofc/quickshell-) | Interface da barra e painéis Quickshell usados com Niri. | Mantém QML, serviços, `niri/barra.kdl`, documentação e harness de teste no próprio repositório. |

A integração entre eles é um submodule externo mais `integration/quickshell.lock.json`; o gitlink e o lock fixam o mesmo commit. O engine ARM64 `quickshell-0:0.3.1-2.fc44` tem pin independente. O README da interface documenta a API original 0.2.1 e a transição do indicador Wi-Fi para `Quickshell.Networking` 0.3.1. Ao alterar essa interface, atualize e revise cada repositório separadamente.

O objetivo gráfico otimizado é uma sessão principal Niri Performance. Gaming é um mode/profile dentro do mesmo compositor: `Gaming Mode == Gamescope ativo`; fora dele, jogo ou Steam pode rodar normalmente no Niri. Não existe sessão Steam independente nem backend `direct-niri` para Gaming Mode. Steam inicia sob demanda; ARM64 nativo roda no host e jogos x86 podem seguir Steam → muvm → FEX → Proton. Consulte `docs/session-architecture.md`. Plasma fica somente como recovery/fallback durante o desenvolvimento: o baseline ajuda a entender o estado atual, identificar processos KDE ausentes em Niri e comparar ganhos, sem tuning de Plasma.

## O que o baseline mostra

A captura em `hardware/mba-m1-8gb/baseline/` foi gerada em 6 de outubro de 2026 no horário local indicado em `generated.txt`. Ela registra Fedora Asahi Remix 44, kernel Asahi `aarch64`, Apple M1 e 8 CPUs (clusters Icestorm e Firestorm). `MemTotal` reporta cerca de 7,3 GiB visíveis ao Linux; esse valor não contradiz os 8 GB físicos, pois é a memória disponível após reservas do hardware.

O estado capturado está em uma sessão Plasma em uso, com navegador, Discover e outros processos abertos. A memória usada na captura não é uma medição de login limpo e não deve ser comparada diretamente com a meta futura de RAM da sessão Niri. `zram.txt` está vazio e `zswap.txt` registra zswap habilitado; o baseline apenas documenta esses valores, sem propor mudanças.

## Codex Cloud → GitHub → M1

1. Faça o desenvolvimento no Cloud, em branch do repositório responsável pela mudança. Mantenha código de sistema em `asahi-system` e código de interface em `quickshell-`.
2. Rode as validações estáticas disponíveis no Cloud. No repositório principal: `python3 scripts/validate_baseline.py` e `python3 -m unittest discover -s tests -v`. O workflow do GitHub Actions repete essas verificações em PRs. O coletor read-only é validado com dados simulados para arquivos ausentes e arquitetura Cloud; isso não valida o hardware Apple.
3. Faça commits pequenos e abra PR. Revise o diff e os resultados do CI antes de integrar; `main` deve continuar representando uma configuração conhecida e revisável.
4. Só depois da revisão use o M1 para testes que dependem do hardware ou da sessão Asahi real. Cloud pode validar formato, scripts e lógica isolada; não comprova kernel, boot, GPU Honeykrisp, Wayland/Niri real, uso de RAM, jogos, FEX/muvm ou desempenho.
5. Se for necessário coletar informação ou executar um teste no M1, mantenha tudo em um único script versionado e entregue um único comando de execução. Registre novas capturas sem substituir o baseline anterior.

Nenhuma otimização ou alteração do sistema físico faz parte deste workflow inicial. Mudanças que afetem Plasma, serviços, pacotes ou os componentes protegidos em `AGENTS.md` precisam de autorização explícita e revisão própria.

O protocolo A/B, interpretação de métricas, categorias observacionais do baseline e desenho futuro das sessões estão em `docs/benchmark-protocol.md`, `docs/service-classification.md` e `docs/session-architecture.md`.

## Ferramentas de validação

O validador do baseline e seus testes usam Python padrão, sem acesso ao M1 nem dependências externas. O README e o `AGENTS.md` de `quickshell-` documentam PySide6 e Pillow para o harness offscreen. PySide6 fornece o runtime Qt Quick usado pelos testes e também inclui `pyside6-qmllint`; Pillow permanece instalado conforme a documentação do harness, embora os scripts atuais não o importem diretamente. Mesmo quando passa, o harness cobre carga/renderização e estados simulados, não D-Bus, rede, compositor ou hardware real. Consulte o README e o `AGENTS.md` daquele repositório para os comandos e limitações atuais.

## Preparação do ambiente Codex Cloud

O baseline pode ser validado com Python 3.12 ou mais recente; os comandos do repositório principal usam apenas a biblioteca padrão:

```bash
cd /workspace/repos/asahi-system
python3 scripts/validate_baseline.py
python3 -m unittest discover -s tests -v
```

Os testes de `asahi-system` usam `kdl-py` fixado em `requirements-test.txt` para parsear KDL; o workflow CI instala essa dependência. Para o harness do repositório separado `quickshell-`, use um virtualenv fora dos checkouts e instale as dependências declaradas pelo próprio repositório:

```bash
python3.12 -m venv /workspace/.venvs/quickshell-tests
cd /workspace/repos/quickshell-
/workspace/.venvs/quickshell-tests/bin/python -m pip install \
  -r requirements-test.txt

QT_QPA_PLATFORM=offscreen /workspace/.venvs/quickshell-tests/bin/python tests/run.py load
QT_QPA_PLATFORM=offscreen /workspace/.venvs/quickshell-tests/bin/python tests/run.py render --all
/workspace/.venvs/quickshell-tests/bin/python tests/test_agenda_lifecycle.py
/workspace/.venvs/quickshell-tests/bin/python tests/test_bluetooth_lifecycle.py
/workspace/.venvs/quickshell-tests/bin/python tests/test_netctl.py
/workspace/.venvs/quickshell-tests/bin/python tests/test_notifications.py
/workspace/.venvs/quickshell-tests/bin/python tests/test_network_indicator.py
/workspace/.venvs/quickshell-tests/bin/python tests/test_niri_integration_contract.py
/workspace/.venvs/quickshell-tests/bin/python -m compileall -q services tests
/workspace/.venvs/quickshell-tests/bin/python tests/lint_qml.py
```

O wheel de PySide6 inclui as ferramentas e módulos Qt necessários ao harness; não é preciso instalar um SDK Qt do sistema para esses comandos. Use `/workspace/.venvs/quickshell-tests/bin/pyside6-qmllint` para o lint Qt 6. Neste Cloud, `/usr/bin/qmllint` é um shim `qtchooser` que aponta para uma instalação Qt 5 ausente. O lint de QML é consultivo: mesmo com os caminhos de módulos gerados pelo harness, o linter reporta avisos porque não conhece todos os tipos Quickshell externos e os tipos registrados pelo harness Python. O comando de carga do harness é a validação QML executável disponível.

Na sessão Cloud investigada, não havia hook persistente de instalação nem configuração de ambiente vinculada ao projeto. O virtualenv acima pode ser reutilizado enquanto o workspace permanecer disponível; em um workspace novo, repita a criação e instalação. A arquitetura Cloud x86_64 não substitui validações que dependem do Fedora Asahi, Apple M1, GPU, Niri/Wayland real ou medidas de desempenho.

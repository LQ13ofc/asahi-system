# Instruções permanentes do projeto

## Escopo e repositórios

- Este repositório, `LQ13ofc/asahi-system`, é a fonte principal da configuração do Fedora Asahi instalado no MacBook Air M1.
- `LQ13ofc/quickshell-` é a interface Quickshell/Niri e permanece um repositório separado. Não duplique seu código em `asahi-system`. A integração atual usa submodule mais `integration/quickshell.lock.json` para fixar um commit; instalação/execução usa checkout externo e symlink, sem cópia de QML. Mudanças nos dois repositórios devem ser feitas e revisadas no histórico, branch e PR de cada repositório.
- O alvo é MacBook Air M1 2020, Apple M1, 8 GB de memória, `aarch64` e Fedora Asahi Remix 44.
- A sessão gráfica principal futura é Niri Performance. Gaming é um mode/profile dentro da mesma sessão Niri, não uma sessão gráfica independente. Não trate componentes ainda não implementados como configuração existente.

## Segurança do sistema

- Preserve o Plasma instalado e inicializável como sessão de recuperação.
- Não remova ou desative Plasma, serviços ou pacotes, nem aplique otimizações, sem uma tarefa explícita que autorize aquela mudança.
- Não instale Niri nem implemente configuração executável de Gaming Mode sem tarefa explícita; documentação conceitual e validação Cloud não autorizam instalação no Mac.
- Não altere kernel, bootloader, Mesa, drivers Asahi, zswap, muvm ou FEX sem autorização explícita para esse componente e um plano de recuperação revisável.
- O Codex Cloud serve para desenvolvimento e verificações estáticas. Não execute nele ações destinadas a mudar o Mac físico e não considere testes em x86 equivalentes a testes no M1.
- Trate `hardware/mba-m1-8gb/baseline/` como evidência histórica imutável. Ao coletar dados novos, adicione uma captura identificada por data em vez de sobrescrever a anterior.
- Se um teste futuro precisar do M1, agrupe coleta e comandos em um único script revisável e forneça um único comando para executá-lo.
- Nunca versionar senhas, tokens, URLs secretas de calendário, chaves privadas ou dados pessoais coletados durante testes.

## Desenvolvimento e Git

- Desenvolva primeiro no Codex Cloud; reserve o M1 para verificações de hardware, Asahi, GPU Honeykrisp, Niri real, consumo de RAM, jogos, FEX/muvm e benchmarks.
- Mantenha `main` como estado conhecido e revisável. Faça mudanças em branches, execute as validações aplicáveis, use commits pequenos e coerentes e abra PR para revisão. Não faça mudanças diretamente em `main`.
- Prefira Python da biblioteca padrão para verificações do repositório. Declare dependências adicionais e mantenha ferramentas de teste isoladas do sistema operacional.
- Scripts de preparação devem explicar o que verificam e não alterar o sistema por padrão. Separe coleta, validação e aplicação de configuração.
- Documente limites de cada teste: validação estática não prova funcionamento no kernel Asahi, na GPU, em Wayland/Niri real ou desempenho de RAM.

## Benchmark e experimentos

- A ordem de trabalho é: baseline → hipótese → uma mudança → benchmark A/B → comparação → decisão `KEEP`, `REVERT` ou `INCONCLUSIVE` → documentação → commit → PR.
- Trate recomendações externas como hipóteses. Não combine mudanças num experimento nem conclua com base em uma única captura.
- Para idle: login limpo, sem Brave/Steam/terminal adicional, aguarde 2–3 minutos e colete cinco execuções. Compare mediana e dispersão com condições equivalentes. Nunca use `drop_caches`.
- `scripts/collect-performance-baseline` é read-only. Métricas ausentes devem permanecer explícitas como `AVAILABLE`, `UNAVAILABLE`, `PERMISSION_REQUIRED` ou `NOT_APPLICABLE`; ausência não é zero. Nunca some RSS para estimar memória global.
- A classificação de serviços é observacional e não autoriza remoção ou desativação. `UNKNOWN` significa manter e investigar. Nunca altere `speakersafetyd` (coleta read-only de estado é permitida); TuneD, boot/kernel, Mesa/Honeykrisp, swap/zswap, MGLRU, cgroups de produção, journald, NetworkManager, WirePlumber, muvm e FEX permanecem protegidos sem tarefa explícita e plano revisável.
- Plasma é somente recovery durante o desenvolvimento. Use seu baseline para entender o estado existente, identificar processos KDE que não pertencem à Niri e comparar ganhos; não faça tuning de Plasma.
- `Gaming Mode` significa Gamescope ativo dentro da sessão Niri. Jogos/Steam podem rodar normalmente no Niri fora desse mode; se Gamescope não estiver disponível, não chame o caso de Gaming Mode, não faça fallback automático e não crie backend `direct-niri`. Gamescope continua no objetivo e pode virar o wrapper preferido depois de compatibilidade e benchmark. Não invente flags Gamescope antes de validação no hardware.
- O fluxo conceitual do mode deve ser reversível e não parar NetworkManager, PipeWire/WirePlumber, segurança ou serviços Asahi essenciais. Considere otimizações de Quickshell, animações, perfis de recursos, resolução/FPS e cgroups somente após benchmark.
- Xwayland-satellite permanece on-demand pelo mecanismo do Niri. Gaming ARM64 nativo roda no host; Steam inicia sob demanda e x86 segue Steam → muvm → FEX → Proton quando necessário.

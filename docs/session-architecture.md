# Arquitetura planejada da sessão Niri

Esta página descreve a arquitetura atual até B2 e separa o que já está integrado do que continua futuro. A primeira sessão real Niri+Quickshell iniciou no M1 e a barra apareceu; Command+Space, áudio/rede/suspend e a verificação completa dos autostarts KDE ainda precisam de confirmação no hardware.

```text
Fedora Asahi
└── Niri Performance
    ├── Niri
    ├── Quickshell (código permanece em LQ13ofc/quickshell-)
    ├── PipeWire + pipewire-pulse + WirePlumber
    ├── NetworkManager
    ├── polkit agent, portal necessário e serviços mínimos
    └── Gaming Mode (continua dentro do mesmo Niri)
        ├── Gamescope obrigatório enquanto o mode estiver ativo
        ├── Steam/jogo iniciado sob demanda
        ├── ARM64 nativo no host ou Steam → muvm → FEX → Proton
        ├── perfil de resolução/FPS/recursos validado
        └── restauração reversível ao sair
```

## B1 — compositor e ferramentas base

O conjunto base mantém Niri, foot, fuzzel, GTK portal e agente PolicyKit condicionado à sessão Niri. PipeWire/WirePlumber e NetworkManager permanecem componentes existentes do Fedora Asahi. Niri+ reutiliza a entrada de sessão empacotada pelo Fedora; não instala uma entrada custom duplicada. Consulte [`docs/niri-phase-b.md`](niri-phase-b.md) para pacotes, lifecycle e validações.

## B2 — Quickshell integrado

Na sessão Niri, `graphical-session.target` é dono da unidade `asahi-quickshell.service`, que inicia o checkout visual fixado. Quickshell não tem segundo `spawn-at-startup` nem é iniciado no Plasma. Os componentes base permanecem separados entre o engine instalado por Niri+ e o código visual no repo quickshell-. Jogos e Steam podem rodar diretamente nessa sessão normal, sem serem chamados de Gaming Mode.

Xwayland-satellite continua on-demand pelo mecanismo do Niri, não como processo permanente presumido. O ciclo de vida dos painéis, polling e serviços Quickshell deve ser medido antes de qualquer suspensão ou lazy loading.

## Gaming Mode: Gamescope obrigatório

Gaming Mode é um mode/profile dentro do Niri e **só existe com Gamescope ativo**:

```text
Niri → Gamescope → Steam/jogo → muvm → FEX → Proton (quando necessário)
```

O jogo pode ser ARM64 nativo no host ou Steam/muvm/FEX/Proton para x86 conforme o título. Gamescope pode envolver Steam ou apenas o jogo, incluindo Steam Gamepad UI, quando essa topologia for tecnicamente compatível e trouxer benefício medido. Não existe fallback automático nem backend `direct-niri` dentro do Gaming Mode. Se Gamescope não puder iniciar, o jogo permanece no fluxo normal Niri fora do mode.

O blocker upstream reportado para Asahi/kmsro é [ValveSoftware/gamescope#1966](https://github.com/ValveSoftware/gamescope/issues/1966). Compatibilidade ainda precisa ser validada no M1 e na pilha Asahi real; esta referência não autoriza inventar flags nem declarar suporte. Mantenha uma separação de implementação específica para Gamescope, por exemplo:

```text
gaming/
├── launcher/
├── profiles/
└── backends/
    └── gamescope/
```

Não criar backend alternativo para definir Gaming Mode. Até Gamescope funcionar, Gaming Mode fica bloqueado; jogos seguem disponíveis no uso normal do Niri.

## Transição reversível

O fluxo desejado é: solicitar jogo → ativar o perfil → iniciar Gamescope → iniciar Steam/jogo → aguardar encerramento → encerrar apenas os processos do escopo de jogo, se isso estiver configurado → restaurar o perfil normal do Niri. As ações concretas dependem de benchmark e devem sempre ter restauração. Não matar serviços essenciais nem parar NetworkManager, PipeWire/WirePlumber, segurança ou serviços Asahi essenciais.

Pontos que podem ser avaliados individualmente com A/B: suspender polling visual não necessário do Quickshell; descarregar painéis pesados; pausar animações/efeitos opcionais, Cava e updates de clima/calendário/stats; scope systemd do jogo; `memory.low` para proteger Niri/PipeWire; resolução, FPS cap e render resolution. Nada disso é aplicado nesta fase.

## Comparação exigida quando o hardware permitir

- A: Niri → jogo direto (modo normal).
- B: Niri → Gamescope → jogo.
- C: Niri → Gamescope → Steam Gamepad UI → jogo, se fizer sentido para o título.

Compare RAM/PSS, CPU, GPU, FPS médio, 1% low, frametime, swap, PSI, latency e estabilidade em condições equivalentes. Prefira Gamescope como caminho de Gaming Mode somente após compatibilidade e evidência de qualidade; a definição semântica do mode permanece “Gamescope ativo”.

## Plasma e limites

Plasma não é alvo de otimização. Fica instalado apenas como recovery/fallback durante o desenvolvimento. Use seu baseline para entender o estado atual, identificar processos KDE que não devem aparecer em Niri e medir ganhos da nova sessão; não desenhe tuning para KDE/Plasma.

O pin escolhido é o gitlink do submodule mais `integration/quickshell.lock.json`, ambos no commit `55e92880d0aff75d235f283c839ec0990eaa9e17` (PR #3, pendente de revisão). O rollback do updater de origem ainda não existe; `niri+ update` continua stub seguro, enquanto `sudo niri+ install` atualiza o clone oficial `main` por fast-forward e reinstala a CLI. Nenhum pacote ou serviço global é removido ou desativado por esta documentação.

# Arquitetura planejada da sessão Niri

Esta página descreve o alvo completo e separa o que está preparado no B1 do que continua futuro. O B1 tem configuração revisável e instalador em PR; nada foi instalado nem altera o baseline.

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

## B1 — sessão mínima preparada

B1 é Niri puro: Niri, foot, fuzzel, GTK portal e agente PolicyKit condicionado à sessão Niri. PipeWire/WirePlumber e NetworkManager permanecem os componentes já existentes no Fedora Asahi; o instalador não os habilita nem reconfigura. Quickshell, notificações, screenshot/clipboard helpers, wallpaper e ajustes de performance ficam fora do primeiro ensaio. Consulte [`docs/niri-phase-b.md`](niri-phase-b.md) para pacotes, lifecycle, instalação e validações.

## Sessão normal desejada após B2

Niri é a única sessão gráfica otimizada principal. No uso normal, Niri executa Quickshell e os componentes necessários (PipeWire, `pipewire-pulse`, WirePlumber, NetworkManager, agente polkit, portal necessário e serviços comprovadamente essenciais). Jogos e Steam podem rodar diretamente nessa sessão normal, sem serem chamados de Gaming Mode.

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

A integração/pinning de `quickshell-` será escolhida em tarefa futura, mantendo o repositório separado e fixando a versão usada em um commit. Nenhum pacote ou serviço é habilitado, removido ou desativado por esta documentação.

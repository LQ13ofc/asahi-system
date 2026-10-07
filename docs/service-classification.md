# Classificação observacional dos serviços do baseline

Fonte: units ativas listadas em `hardware/mba-m1-8gb/baseline/system-services.txt` e `user-services.txt`, captura de 2026-10-06 em uma sessão Plasma ocupada. O CSV mantém cada unit como foi registrada, scope, estado e descrição. Há **83 units observadas**: 15 `ESSENTIAL`, 23 `SESSION`, 10 `ON_DEMAND`, 10 `OPTIONAL`, 11 `CANDIDATE`, 3 `DO_NOT_TOUCH` e 11 `UNKNOWN`.

As categorias organizam perguntas futuras; não são uma ordem de mudança nem uma afirmação de que serviço desconhecido é removível. `ESSENTIAL` aqui significa “preservar como parte estrutural do baseline”, e deve continuar sendo verificado para cada sessão. `CANDIDATE` só indica investigação com hipótese e A/B. Nenhum serviço foi alterado.

## Componentes pedidos para destaque

| Grupo observado no baseline | Units/processos registrados | Classificação inicial | Nota |
|---|---|---|---|
| Plasma/KDE | `plasma-plasmashell`, `plasma-kwin_wayland`, `plasma-kded6`, `plasma-ksmserver`, `plasma-powerdevil` | `SESSION` | Pertencem à sessão Plasma. Preservar Plasma como recovery; a sessão Niri não deve iniciar a sessão Plasma inteira. |
| Plasma/KDE | Discover notifier, Kalendar reminders, Akonadi e serviços KDE PIM | `OPTIONAL` | São funções da sessão em uso. Avaliar necessidade na Niri e comportamento de autostart depois; sem removê-los. |
| Plasma/KDE | Baloo | `CANDIDATE` | Indexação ativa no snapshot; eventual impacto precisa de teste e consideração do recurso de busca. |
| Plasma/KDE | KDE Connect | `CANDIDATE` | Uso pessoal e integrações devem ser mantidos se necessários. |
| Plasma/KDE | Xwayland Video Bridge | `CANDIDATE` | Pode suportar captura/compatibilidade X11; não presumir dispensável. |
| Sistema | PackageKit, CUPS, ModemManager, Avahi, pcscd | `ON_DEMAND` | Capacidades de atualização, impressão, modem, descoberta e smartcard. Checar ativação por demanda/dependências antes de qualquer proposta. |
| Sistema | gssproxy, ABRT, rsyslog, systemd-homed, irqbalance, iio-sensor-proxy, uresourced | `CANDIDATE`, `OPTIONAL` ou `UNKNOWN` | A função varia; veja classificação individual no CSV. Não inferir segurança de desativação. |
| Sistema protegido | `speakersafetyd.service` | `DO_NOT_TOUCH` | Nunca tocar. |
| Sistema protegido | `tuned.service`, `tuned-ppd.service` | `DO_NOT_TOUCH` | TuneD está fora dos limites desta fase. |

## Contagens detalhadas

- `ESSENTIAL` (15): unidades estruturais do sistema, login, barramento, rede, segurança e sessão de usuário como observadas. Não é autorização para trocar sua configuração.
- `SESSION` (23): unidades de Plasma, PipeWire/WirePlumber e integração do desktop.
- `ON_DEMAND` (10): recursos reconhecíveis que podem depender de uso, socket activation ou política da distribuição.
- `OPTIONAL` (10): auxiliares de desktop/relato cujo valor depende do uso.
- `CANDIDATE` (11): pontos de investigação, nunca itens automaticamente dispensáveis.
- `DO_NOT_TOUCH` (3): speakersafetyd e TuneD/tuned-ppd.
- `UNKNOWN` (11): itens transitórios ou com dependências ainda não validadas.

O baseline mistura processos do usuário (Brave/terminal), Plasma, Discover e KDE PIM. Não é um login idle. Os serviços listados permaneceram ativos e a classificação não muda esse estado. Consulte [`baseline-service-classification.csv`](baseline-service-classification.csv) para a relação completa.

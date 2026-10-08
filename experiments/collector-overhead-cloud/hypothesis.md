# Hipótese

O coletor capturava o contador de CPU do sistema antes de ler unidades/cgroups e só capturava o contador final depois de varrer todos os PIDs e `smaps_rollup`. Assim, o próprio trabalho do observador podia aparecer como CPU busy e o denominador da porcentagem não correspondia exatamente à janela solicitada. Delimitar os contadores/PSI/cgroups ao redor da janela, fora das varreduras completas, deve impedir essa contaminação; a métrica de CPU por processo precisa usar um intervalo de scans próprio.

# CLAUDE.md

## Contesto di sessione (da rileggere sempre, anche dopo `/clear`)

- In questa sessione si disegna con **FreeCAD tramite freecad-mcp**, eseguito sul
  **behemoth**, raggiunto passando da **nativedev** (MCP server `Claude_Nativedev_MCP`).
- Non si lavora in locale nel container cloud: ogni operazione CAD passa dai tool
  `mcp__Claude_Nativedev_MCP__*` (`run_command` per comandi semplici,
  `claude_execute` per task complessi, `file_read`/`file_write` per i file).
  Se non sono caricati, caricarli con ToolSearch (`select:mcp__Claude_Nativedev_MCP__run_command`, ecc.).
- Il connettore Onshape (`Onshape-MOP`) non è il canale di disegno per questa sessione.

### Topologia (tutti gli host sono sulla tailnet Tailscale `tail7d3518.ts.net`)

| Host | IP Tailscale | Ruolo |
|------|--------------|-------|
| nativedev | `100.112.35.102` (`nativedev.tail7d3518.ts.net`) | Punto di controllo: qui gira il client `freecad-mcp` e il bridge |
| behemoth (`tommaso-behemoth-1`) | `100.103.234.2` | Workstation con FreeCAD (snap) + addon RPC freecad-mcp — **target di disegno** |
| fujiyama (`tommaso-fujiyama-1`) | `100.85.88.58` | Altro host FreeCAD |
| native-beast | `100.115.135.14` | Altro host FreeCAD |
| thinkpad (`tommaso-thinkpad-t14s-gen-6`) | `100.97.139.3` | Altro host FreeCAD (flatpak) |

Fonte della topologia su nativedev: `~/MASTER_CONTEXT.md` (sezione "RETE TAILSCALE"),
`tailscale status`, `~/projects/cad-3d/freecad-mcp/CHAT_LOG.md`.

### Come si raggiunge FreeCAD sul behemoth

- Addon freecad-mcp dentro FreeCAD: server **XML-RPC su `100.103.234.2:9875`**
  (parte in automatico all'apertura di FreeCAD, `auto_start_rpc: true`).
  Se la connessione è rifiutata, FreeCAD non è aperto sul behemoth (o l'RPC non è avviato).
- Client MCP su nativedev: `~/.local/bin/freecad-mcp --host 100.103.234.2 --only-text-feedback`
  (registrato come `freecad-behemoth`; config in `~/.config/freecad-mcp-configs/*.json`).
- SSH da nativedev: `ssh behemoth` (→ `tommaso@100.103.234.2`, in `~/.ssh/config`).
- Sul behemoth FreeCAD è installato via snap: addon/macro in `~/snap/freecad/common/Mod/`.
- Progetto freecad-mcp su nativedev: `~/projects/cad-3d/freecad-mcp/`
  (bridge FastAPI `freecad_claude_bridge.py` su `:7891`, unit systemd user
  `freecad-claude-bridge.service`, setup `setup_freecad_mcp.sh`).
- Check rapido da nativedev:
  `python3 -c "import xmlrpc.client as x;print(x.ServerProxy('http://100.103.234.2:9875').ping())"`

### Gotcha noti (FreeCAD snap sul behemoth)

- FreeCAD snap vede `~` = `~/snap/freecad/<rev>/` e ha `/tmp` privato: i file salvati da
  `execute_code` con `expanduser("~/...")` finiscono in `~/snap/freecad/<rev>/...`.
  Usare path assoluti sotto `/home/tommaso/snap/freecad/common/` oppure copiarli dopo.
- **Non usare `ActiveView.saveImage()` via RPC**: blocca il thread GUI e l'RPC smette di
  rispondere (serve riavviare FreeCAD). Per le anteprime renderizzare gli STL su nativedev
  (`uv run --no-project --with matplotlib --with numpy-stl ...`).
- Avvio FreeCAD da nativedev: `ssh behemoth 'systemd-run --user --unit=freecad-gui --collect /snap/bin/freecad [file.FCStd]'`
  (riavvio: `systemctl --user stop freecad-gui`).

### Lavori in corso

- Clip snap-fit per bilanciere Ø25 (riferimento centro + prese panca):
  script parametrico `~/projects/cad-3d/clip_bilanciere/clip.py` (su behemoth e nativedev),
  output (scritto da FreeCAD in `~/snap/freecad/common/clip_bilanciere/`, poi copiato)
  `Clip_Bilanciere.FCStd`, `Clip_Centro.{stl,step}` (inciso PL), `Clip_Mano.{stl,step}` (inciso SM).
  Alette pentagonali raccordate R1.5, incisione 0.6 mm sulla faccia superiore dell'aletta.

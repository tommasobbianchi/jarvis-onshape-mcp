# CLAUDE.md

## Contesto di sessione (da rileggere sempre, anche dopo `/clear`)

- In questa sessione si disegna con **FreeCAD tramite freecad-mcp**, eseguito sul
  **behemoth**, raggiunto passando da **nativedev** (MCP server `Claude_Nativedev_MCP`).
- Non si lavora in locale nel container cloud: ogni operazione CAD passa dai tool
  `mcp__Claude_Nativedev_MCP__*` (`run_command` per comandi semplici,
  `claude_execute` per task complessi, `file_read`/`file_write` per i file).
  Se non sono caricati, caricarli con ToolSearch (`select:mcp__Claude_Nativedev_MCP__run_command`, ecc.).
- Riferimenti sulla macchina remota (utente `tommaso`, hostname `nativedev`):
  - bridge freecad-mcp: `~/projects/cad-3d/freecad-mcp/freecad_claude_bridge.py` (processo già in esecuzione)
  - FreeCAD installato via snap: `/snap/freecad/current/usr/bin/FreeCAD`
  - altro progetto correlato: `~/projects/freecad-panel`
- Il connettore Onshape (`Onshape-MOP`) non è il canale di disegno per questa sessione.

# openosint.tape — reproducible terminal demo (~19s, loops cleanly)
# Run: vhs openosint.tape

Output assets/demo.gif

Set Shell "bash"
Set FontSize 26
Set FontFamily "JetBrains Mono"
Set Width 1200
Set Height 700
Set Theme "Catppuccin Mocha"
Set Padding 24
Set TypingSpeed 60ms

# --- hidden setup: clean prompt, fixture-backed alias ---
Hide
Type "export PS1='$ '"
Enter
Sleep 150ms
Type "alias openosint='./scripts/openosint_vhs_demo.sh'"
Enter
Sleep 150ms
Show
# --------------------------------------------------------

Sleep 1000ms

# Scene 1 — username scan across 300+ platforms
Type "openosint username johndoe99"
Enter
Sleep 4200ms

# Scene 2 — email account enumeration
Type "openosint email target@example.com"
Enter
Sleep 3500ms

# Scene 3 — MCP integration (shown as comment so nothing executes)
Type "# claude mcp add openosint -- python3 -m openosint.mcp_server"
Enter

Sleep 2500ms

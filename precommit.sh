#!/bin/bash
HOOK_FILE=".git/hooks/pre-commit"

cat > "$HOOK_FILE" << 'EOF'
#!/bin/bash
./script.sh
EOF

chmod +x "$HOOK_FILE"
echo "pre-commit hook created!"


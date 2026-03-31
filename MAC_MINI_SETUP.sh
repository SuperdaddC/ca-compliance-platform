# Mac Mini Server Setup — ComplyWithJudy Scanner
# Replaces AWS Lambda with a persistent FastAPI server on your Mac Mini
# Total cost: $0/month (just your electricity)

# ================================================================
# STEP 1: Install Python dependencies on Mac Mini
# ================================================================

# Open Terminal on your Mac Mini and run:

cd ~/complywithjudy-backend

# Create a virtual environment (keeps dependencies isolated)
python3 -m venv venv
source venv/bin/activate

# Install everything
pip install fastapi uvicorn playwright httpx python-dotenv

# Install Playwright's browser (Chromium only — smaller download)
playwright install chromium

# ================================================================
# STEP 2: Environment variables
# Create a file called .env in your backend folder
# NEVER commit this file to GitHub
# ================================================================

cat > .env << 'EOF'
SUPABASE_URL=https://mvdqlttptgwndoccotgg.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key_here   # Get from Supabase dashboard > Settings > API > service_role
ALLOWED_ORIGINS=https://complywithjudy.com
STRIPE_SECRET_KEY=sk_live_YOUR_KEY
STRIPE_WEBHOOK_SECRET=whsec_YOUR_SECRET
EOF

# ================================================================
# STEP 3: Create a launchd service so the scanner auto-starts
# (launchd is macOS's equivalent of a startup service — it runs
#  your scanner automatically whenever the Mac Mini boots)
# ================================================================

cat > ~/Library/LaunchAgents/com.complywithjudy.scanner.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.complywithjudy.scanner</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/ColyerTeam/complywithjudy-backend/venv/bin/uvicorn</string>
    <string>scanner:app</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>8000</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/ColyerTeam/complywithjudy-backend</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>

  <!-- Auto-restart if it crashes -->
  <key>KeepAlive</key>
  <true/>

  <!-- Start on login -->
  <key>RunAtLoad</key>
  <true/>

  <!-- Log output -->
  <key>StandardOutPath</key>
  <string>/tmp/complywithjudy-scanner.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/complywithjudy-scanner-error.log</string>
</dict>
</plist>
PLIST

# Load the service (starts it immediately)
launchctl load ~/Library/LaunchAgents/com.complywithjudy.scanner.plist

# Check it's running
curl http://localhost:8000/health
# Should return: {"status":"ok","version":"2.0.0"}

# ================================================================
# STEP 4: Cloudflare Tunnel
# This gives your Mac Mini a permanent public HTTPS URL
# (e.g. scanner.complywithjudy.com) without opening firewall ports
# Cost: FREE
# ================================================================

# Install cloudflared
brew install cloudflared

# Authenticate with your Cloudflare account
# (complywithjudy.com must be on Cloudflare — it's free)
cloudflared tunnel login

# Create a tunnel named "scanner"
cloudflared tunnel create scanner

# Configure the tunnel
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml << 'CONFIG'
tunnel: scanner
credentials-file: /Users/ColyerTeam/.cloudflared/YOUR_TUNNEL_ID.json

ingress:
  - hostname: scanner.complywithjudy.com
    service: http://localhost:8000
  - service: http_status:404
CONFIG

# Add DNS record in Cloudflare (this maps scanner.complywithjudy.com → your Mac)
cloudflared tunnel route dns scanner scanner.complywithjudy.com

# Create launchd service for the tunnel too
cat > ~/Library/LaunchAgents/com.complywithjudy.tunnel.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.complywithjudy.tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/cloudflared</string>
    <string>tunnel</string>
    <string>run</string>
    <string>scanner</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/cloudflare-tunnel.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/cloudflare-tunnel-error.log</string>
</dict>
</plist>
PLIST

launchctl load ~/Library/LaunchAgents/com.complywithjudy.tunnel.plist

# ================================================================
# STEP 5: Update Netlify environment variable
# In Netlify dashboard: Site settings > Environment variables
# Add: VITE_SCANNER_URL = https://scanner.complywithjudy.com
# ================================================================

# ================================================================
# STEP 6: Update Stripe webhook URL
# In Stripe dashboard: Developers > Webhooks
# Change endpoint to: https://scanner.complywithjudy.com/stripe-webhook
# ================================================================

# ================================================================
# Useful commands going forward
# ================================================================

# View scanner logs live:
#   tail -f /tmp/complywithjudy-scanner.log

# Restart scanner after code changes:
#   launchctl stop com.complywithjudy.scanner
#   launchctl start com.complywithjudy.scanner

# Check tunnel status:
#   cloudflared tunnel info scanner

# Test the public endpoint:
#   curl https://scanner.complywithjudy.com/health

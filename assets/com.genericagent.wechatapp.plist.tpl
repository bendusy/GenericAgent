<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.genericagent.wechatapp</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{PYTHON}}</string>
    <string>{{REPO}}/frontends/wechatapp.py</string>
  </array>
  <key>WorkingDirectory</key><string>{{REPO}}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{{LOG_DIR}}/wechatapp.out.log</string>
  <key>StandardErrorPath</key><string>{{LOG_DIR}}/wechatapp.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{{LARK_BIN_DIR}}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>LARK_CLI</key><string>{{LARK_CLI_PATH}}</string>
  </dict>
  <key>ThrottleInterval</key><integer>30</integer>
</dict>
</plist>
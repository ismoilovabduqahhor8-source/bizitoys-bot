// ============================================================
//  PM2 konfiguratsiyasi (systemd o'rniga ishlatsangiz).
//
//  O'rnatish:
//    npm install -g pm2
//    pm2 start deploy/ecosystem.config.js
//    pm2 save && pm2 startup     // server qayta yuklansa ham ishlaydi
//    pm2 logs bizitoys-bot
// ============================================================
module.exports = {
  apps: [
    {
      name: "bizitoys-bot",
      script: ".venv/bin/python",
      args: "main.py",
      cwd: "/opt/bizitoys_bot",
      interpreter: "none",
      autorestart: true,
      max_restarts: 50,
      restart_delay: 5000,
      max_memory_restart: "300M",
      time: true,
      error_file: "logs/error.log",
      out_file: "logs/out.log",
    },
  ],
};

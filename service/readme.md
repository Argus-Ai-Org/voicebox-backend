sudo nano /etc/systemd/system/voicebox.service

sudo systemctl daemon-reload

sudo systemctl enable voicebox

sudo systemctl start voicebox

journalctl -u voicebox -f

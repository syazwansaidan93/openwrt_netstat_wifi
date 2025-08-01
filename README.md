# OpenWrt Network Monitor

This project is a complete solution for monitoring network traffic on an OpenWrt router. It consists of a Python script for data collection, a PHP API to serve the data, and a set of configuration files for deployment on an Orange Pi Zero 3.

---

## Files and Scripts

### 1. Python Data Collector (`openwrt-collector.py`)

The Python data collector script is already available in this repository. It runs hourly to collect real-time traffic statistics and DHCP lease information from your OpenWrt router, storing the data in an SQLite database.

### 2. Python Configuration (`config.json`)

This file holds the router IP addresses and database file path for the Python script.

```json
{
    "ROUTER_URLS": [
        "[http://192.168.1.1/cgi-bin/totalwifi.cgi](http://192.168.1.1/cgi-bin/totalwifi.cgi)",
        "[http://192.168.1.2/cgi-bin/totalwifi.cgi](http://192.168.1.2/cgi-bin/totalwifi.cgi)"
    ],
    "DHCP_LEASE_URLS": [
        "[http://192.168.1.1/cgi-bin/dhcp.cgi](http://192.168.1.1/cgi-bin/dhcp.cgi)"
    ],
    "DATABASE_FILE": "openwrt_traffic.db",
    "DAILY_RUN_HOUR": 3
}
```

### 3. PHP API (`api.php`)

The PHP API script is also available in this repository. It exposes the data from the SQLite database via a web API.

### 4. Nginx Configuration

This configuration file tells Nginx how to serve your PHP API.

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    root /var/www/html/netstat;
    index index.html index.htm index.nginx-debian.html index.php;
    server_name _;
    location / {
        try_files $uri $uri/ =404;
    }
    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/var/run/php/php7.4-fpm.sock;
    }
    location ~ /\. {
        deny all;
    }
}
```

### 5. OpenWrt CGI Script

This script should be deployed on your OpenWrt router to provide the traffic data.

```sh
#!/bin/sh

# This is a CGI script for OpenWrt to dump network traffic data
# in a format that can be consumed by the Python data collector.

echo "Content-type: text/plain"
echo ""

# Get traffic data for all wireless clients connected to any interface.
# The script iterates through each wireless interface and dumps the station
# data, then uses awk to extract the MAC, RX bytes, and TX bytes.
for iface in $(iw dev | awk '$1=="Interface"{print $2}'); do
    iw dev "$iface" station dump | awk '
        $1=="Station" {mac=$2}
        $1=="rx" && $2=="bytes:" {rx=$3}
        $1=="tx" && $2=="bytes:" {tx=$3; print mac, rx, tx}
    '
done

# Get the traffic data for the WAN interface.
# This looks for the "wan:" entry in /proc/net/dev to find the total
# received (RX) and transmitted (TX) bytes. The awk command prints the
# interface name, RX bytes, and TX bytes.
awk '$1=="wan:"{print "wan:", $2, $10}' /proc/net/dev
```

### 6. `systemd` Service and Timer

These files automate the execution of the Python script on your Orange Pi Zero 3.

**Service File (`/etc/systemd/system/openwrt-collector.service`)**
```ini
[Unit]
Description=OpenWrt Network Data Collector
After=network.target

[Service]
User=wan
Group=wan
WorkingDirectory=/home/wan/openwrt/logger/
ExecStart=/home/wan/openwrt/logger/.venv/bin/python /home/wan/openwrt/logger/openwrt-collector.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**Timer File (`/etc/systemd/system/openwrt-collector.timer`)**
```ini
[Unit]
Description=Run OpenWrt Network Data Collector hourly

[Timer]
OnCalendar=hourly
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
```

## Setup and Deployment

1. **Install Prerequisites:** On your Orange Pi, run `sudo apt update && sudo apt install python3 python3-pip nginx php-fpm php-pdo php-sqlite3 -y`.
2. **Clone the Repository:** Download these files to their specified locations.
3. **Python Setup:** Navigate to `/home/wan/openwrt/logger`, create a virtual environment (`python3 -m venv .venv`), activate it (`source .venv/bin/activate`), and install the `requests` library (`pip install requests`).
4. **OpenWrt Router:** Save the OpenWrt CGI script to `/www/cgi-bin/totalwifi.cgi` and make it executable (`chmod +x /www/cgi-bin/totalwifi.cgi`).
5. **Configure Nginx:** Update the `/etc/nginx/sites-available/default` file with the Nginx configuration provided above. Test and reload (`sudo nginx -t && sudo systemctl reload nginx`).
6. **Set Permissions:** Grant Nginx access to the database by running `sudo chown -R www-data:www-data /home/wan/openwrt/logger && sudo chmod 755 /home/wan /home/wan/openwrt /home/wan/openwrt/logger`.
7. **Enable Systemd:** Reload the daemon and enable the timer (`sudo systemctl daemon-reload && sudo systemctl enable openwrt-collector.timer && sudo systemctl start openwrt-collector.timer`).

## Usage

Access the data from your web browser or any client with the following API endpoints:

* **Dashboard data (total traffic):**
  `http://<your_orange_pi_ip>/api.php?type=dashboard`

* **DHCP leases:**
  `http://<your_orange_pi_ip>/api.php?type=leases`

* **Default (same as dashboard):**
  `http://<your_orange_pi_ip>/api.php`

# OpenWrt Traffic and DHCP Collector

This project provides a robust solution for collecting, storing, and exposing network traffic and DHCP lease data from your OpenWrt routers. It consists of a Python script for data collection and an SQLite database for storage, complemented by a PHP API for easy data access via JSON.

## Features

* **Traffic Data Collection:** Gathers daily RX/TX bytes per device (and WAN interface) from multiple OpenWrt routers.

* **DHCP Lease Collection:** Records unique device MAC addresses, their assigned IP addresses, and hostnames **from your main OpenWrt router only (e.g., `192.168.1.1`)**. Only new devices are added to the lease database.

* **SQLite Database:** Stores all collected data in a lightweight, file-based SQLite database.

* **PHP JSON API:** Provides RESTful endpoints to access collected traffic and DHCP lease data.

* **Systemd Scheduling:** Automates data collection at specified intervals (e.g., hourly, daily) for reliable operation.

* **Data Retention:** Automatically prunes old traffic data based on a configurable retention period.

## Project Structure

* `/var/www/openwrt_collector/`: Contains the Python script (`openwrt_logger.py`), its virtual environment (`venv/`), and the SQLite database (`openwrt_traffic.db`).

* `/var/www/html/openwrt_traffic/`: Contains the PHP API script (`api.php`).

## Prerequisites

* A Linux server (e.g., Ubuntu, Debian) with `sudo` access.

* Nginx web server installed and configured.

* PHP 8.2 (or compatible version) with PHP-FPM installed and configured.

* OpenWrt routers accessible from your server.

* **OpenWrt CGI Script for DHCP Leases:** On your main OpenWrt router (e.g., `192.168.1.1`), create `/etc/cgi-bin/dhcp.cgi` with the following content and make it executable:

    ```bash
    #!/bin/sh
    echo "Content-type: text/plain"
    echo ""
    cat /tmp/dhcp.leases
    ```

* **OpenWrt CGI Script for Traffic Data (Main Router):** On your main OpenWrt router (e.g., `192.168.1.1`), create `/etc/cgi-bin/totalwifi.cgi` with the following content and make it executable. This script collects per-device traffic and WAN traffic.

    ```bash
    #!/bin/sh
    echo "Content-type: text/plain"
    echo ""

    for iface in $(iw dev | awk '$1=="Interface"{print $2}'); do
        iw dev "$iface" station dump | awk '
            $1=="Station" {mac=$2}
            $1=="rx" && $2=="bytes:" {rx=$3}
            $1=="tx" && $2=="bytes:" {tx=$3; print mac, rx, tx}
        '
    done
    awk '$1=="wan:"{print "wan:", $2, $10}' /proc/net/dev
    ```

* **OpenWrt CGI Script for Traffic Data (Second Router):** On your second OpenWrt router (e.g., `192.168.1.2`), create `/etc/cgi-bin/totalwifi.cgi` with the following content and make it executable. This script collects per-device traffic.

    ```bash
    #!/bin/sh
    echo "Content-type: text/plain"
    echo ""

    for iface in $(iw dev | awk '$1=="Interface"{print $2}'); do
        iw dev "$iface" station dump | awk '
            $1=="Station" {mac=$2}
            $1=="rx" && $2=="bytes:" {rx=$3}
            $1=="tx" && $2=="bytes:" {tx=$3; print mac, rx, tx}
        '
    done
    ```

## Installation and Deployment

Follow these steps to deploy the entire system on your server.

### 1. Prepare Your Server Environment

```bash
# Create project directories
sudo mkdir -p /var/www/openwrt_collector
sudo mkdir -p /var/www/html/openwrt_traffic

# Set initial ownership for easier file placement
sudo chown -R $USER:$USER /var/www/openwrt_collector
sudo chown -R $USER:$USER /var/www/html/openwrt_traffic

# Install Python Virtual Environment (if not already installed)
sudo apt update
sudo apt install python3-venv
```

### 2. Set Up Python Virtual Environment and Script

```bash
# Navigate to the Python collector directory
cd /var/www/openwrt_collector

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install requests

# Deactivate virtual environment
deactivate
```

**Place the `openwrt_logger.py` script** (from the project source) into `/var/www/openwrt_collector/`.

### 3. Place the PHP API Script

**Place the `api.php` script** (from the project source) into `/var/www/html/openwrt_traffic/`.

**Important:** Ensure the `$databaseFile` path inside `api.php` is correctly set to point to the Python collector directory:
`$databaseFile = '/var/www/openwrt_collector/openwrt_traffic.db';`

### 4. Nginx Configuration

Place the following Nginx server block configuration into `/etc/nginx/sites-available/openwrt_traffic.conf`. **Remember** to replace `your_domain_or_ip` with your actual **domain name or server IP address.**

```nginx
server {
    listen 80;
    server_name your_domain_or_ip;
    root /var/www/html/openwrt_traffic;
    index index.php index.html index.htm;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/var/run/php/php8.2-fpm.sock;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
    }

    location ~ /\.db$ {
        deny all;
        return 404;
    }
}
```

After placing the file, enable it and restart Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/openwrt_traffic.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 5. Set Initial Permissions & Run Python Script Once

This step is crucial for initial database creation and permission setup.

```bash
# Set ownership for the Python collector directory (where the DB will be created)
sudo chown -R root:root /var/www/openwrt_collector
sudo chmod -R 755 /var/www/openwrt_collector

# Set ownership for the PHP web root directory
sudo chown -R www-data:www-data /var/www/html/openwrt_traffic
sudo chmod -R 755 /var/www/html/openwrt_traffic

# Manually run Python script once to create the database
cd /var/www/openwrt_collector
source venv/bin/activate
python openwrt_logger.py
deactivate

# Adjust database file permissions for www-data (PHP-FPM) access
sudo chown www-data:www-data /var/www/openwrt_collector/openwrt_traffic.db
sudo chmod 664 /var/www/openwrt_collector/openwrt_traffic.db
```

### 6. Schedule the Python Script with Systemd

This replaces the cron job for more robust scheduling (e.g., hourly).

**Create the Systemd Service Unit File:**
Create `/etc/systemd/system/openwrt-collector.service` and paste the following content:

```ini
[Unit]
Description=OpenWrt Traffic and DHCP Lease Collector
After=network.target

[Service]
Type=oneshot
ExecStart=/var/www/openwrt_collector/venv/bin/python /var/www/openwrt_collector/openwrt_logger.py
StandardOutput=append:/var/log/openwrt_collector.log
StandardError=append:/var/log/openwrt_collector.log

[Install]
WantedBy=multi-user.target
```

**Create the Systemd Timer Unit File:**
Create `/etc/systemd/system/openwrt-collector.timer` and paste the following content (configured for twice daily runs, adjust `OnCalendar` for hourly if desired: `OnCalendar=*-*-* *:00:00`):

```ini
[Unit]
Description=Run OpenWrt Traffic and DHCP Lease Collector Twice Daily
Requires=openwrt-collector.service

[Timer]
OnCalendar=*-*-* 03:00:00
OnCalendar=*-*-* 15:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

**Reload Systemd, Enable, and Start the Timer:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable openwrt-collector.timer
sudo systemctl start openwrt-collector.timer
```

**Create and Set Permissions for the Log File:**

```bash
sudo touch /var/log/openwrt_collector.log
sudo chmod 644 /var/log/openwrt_collector.log
sudo chown root:root /var/log/openwrt_collector.log
```

### 7. Final Verification

1.  **Access Traffic Data via API:**
    Open your web browser and navigate to:
    `http://your_domain_or_ip/api.php`

2.  **Access DHCP Lease Data via API:**
    Open your web browser and navigate to:
    `http://your_domain_or_ip/api.php?type=leases`

3.  **Access Combined Final Data via API:**
    Open your web browser and navigate to:
    `http://your_domain_or_ip/api.php?type=final`

You should see the JSON output for all endpoints. If you encounter any issues, check your Nginx, PHP-FPM, and `openwrt_collector.log` files for errors.

## API Endpoints

* **`http://your_domain_or_ip/api.php`** (or `?type=traffic`)
    Returns daily traffic data (device MAC, RX/TX bytes) with a `last_updated` timestamp.
    Example Output:

    ```json
    {
        "status": "success",
        "data_type": "traffic",
        "data": [
            { "device_mac": "wan:", "rx_bytes": 12345, "tx_bytes": 67890 },
            { "device_mac": "00:11:22:33:44:55", "rx_bytes": 54321, "tx_bytes": 98765 }
        ],
        "last_updated": "YYYY-MM-DD"
    }
    ```

* **`http://your_domain_or_ip/api.php?type=leases`**
    Returns unique DHCP lease information (MAC address, resolved hostname/IP).
    Example Output:

    ```json
    {
        "status": "success",
        "data_type": "leases",
        "data": [
            { "mac_address": "00:11:22:33:44:55", "hostname": "MyLaptop" },
            { "mac_address": "AA:BB:CC:DD:EE:FF", "hostname": "192.168.1.100" }
        ]
    }
    ```

* **`http://your_domain_or_ip/api.php?type=final`**
    Returns aggregated (summed) traffic data per device over the entire retention period, with MAC addresses replaced by resolved hostnames/IPs, and a `last_updated` timestamp.
    Example Output:

    ```json
    {
        "status": "success",
        "data_type": "final",
        "data": [
            { "hostname": "wan:", "rx_bytes": 1234567890, "tx_bytes": 9876543210 },
            { "hostname": "MyLaptop", "rx_bytes": 1122334455, "tx_bytes": 6677889900 }
        ],
        "last_updated": "YYYY-MM-DD"
    }
    

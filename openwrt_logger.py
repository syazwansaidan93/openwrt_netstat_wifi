import requests
import sqlite3
from datetime import datetime
import os
import re
import json
import time

# --- Configuration File Loading ---
CONFIG_FILE = "config.json"

def load_config():
    """Loads configuration from a JSON file."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(f"Error: {CONFIG_FILE} not found. Please create it with the necessary configuration.")
        return None
    except json.JSONDecodeError:
        print(f"Error: Could not decode {CONFIG_FILE}. Please check the file for JSON syntax errors.")
        return None

# --- Database Setup ---
def setup_database(db_file):
    """
    Connects to the SQLite database and creates the necessary tables
    if they don't already exist.
    """
    try:
        with sqlite3.connect(db_file) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS traffic_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    device_mac TEXT NOT NULL,
                    hourly_rx_bytes INTEGER NOT NULL,
                    hourly_tx_bytes INTEGER NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS total_traffic (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_mac TEXT NOT NULL UNIQUE,
                    total_rx_bytes INTEGER NOT NULL,
                    total_tx_bytes INTEGER NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS last_known_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_mac TEXT NOT NULL UNIQUE,
                    last_rx_bytes INTEGER NOT NULL,
                    last_tx_bytes INTEGER NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dhcp_leases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_timestamp TEXT NOT NULL,
                    router_ip TEXT NOT NULL,
                    mac_address TEXT NOT NULL UNIQUE,
                    ip_address TEXT NOT NULL,
                    hostname TEXT
                )
            ''')
            conn.commit()
            print(f"Database '{db_file}' and tables are ready.")
    except sqlite3.Error as e:
        print(f"Database error during setup: {e}")

# --- Data Fetching and Parsing ---
def fetch_with_retries(url, retries=3, backoff_factor=1):
    """
    Fetches data from a URL with a retry mechanism and exponential backoff.
    """
    for i in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"Attempt {i + 1} failed for {url}: {e}")
            if i < retries - 1:
                wait_time = backoff_factor * (2 ** i)
                print(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"All {retries} attempts failed for {url}.")
                return None

def fetch_and_parse_data(url):
    """
    Fetches traffic data from the given OpenWrt URL and parses it.
    """
    response = fetch_with_retries(url)
    if not response:
        return None, None
        
    router_ip = url.split('//')[1].split('/')[0]
    lines = response.text.strip().split('\n')
    parsed_data = []

    for line in lines:
        parts = line.split()
        if len(parts) == 3:
            device_mac = parts[0].strip()
            try:
                rx_bytes = int(parts[1])
                tx_bytes = int(parts[2])
                parsed_data.append({
                    'device': device_mac,
                    'rx': rx_bytes,
                    'tx': tx_bytes
                })
            except ValueError:
                print(f"Warning: Could not parse RX/TX bytes in line: {line}")
        else:
            print(f"Warning: Skipping malformed line: {line}")
    return router_ip, parsed_data

def fetch_and_parse_dhcp_leases(url):
    """
    Fetches DHCP lease data from the given OpenWrt URL and parses it.
    """
    response = fetch_with_retries(url)
    if not response:
        return None, None

    router_ip = url.split('//')[1].split('/')[0]
    leases_data = []
    mac_regex = re.compile(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')
    
    for line in response.text.strip().split('\n'):
        parts = line.split()
        if not parts or parts[0] == 'duid':
            continue
        if len(parts) < 4:
            print(f"Warning: Skipping malformed DHCP lease line (too few parts): {line}")
            continue

        try:
            second_part = parts[1].strip()
            ip_address = parts[2].strip()
            hostname = parts[3].strip() if parts[3] not in ['*', ''] else None
            unique_identifier = None

            if mac_regex.match(second_part):
                unique_identifier = second_part
            elif len(parts) >= 5 and mac_regex.match(parts[4].strip()):
                unique_identifier = parts[4].strip()
            else:
                if len(parts) >= 5:
                    unique_identifier = parts[-1].strip()
                else:
                    print(f"Warning: Could not determine unique identifier for lease: {line}. Skipping.")
                    continue

            if unique_identifier:
                leases_data.append({
                    'mac_address': unique_identifier,
                    'ip_address': ip_address,
                    'hostname': hostname,
                })
        except (ValueError, IndexError) as e:
            print(f"Warning: Error parsing DHCP lease line '{line}': {e}")
        except Exception as e:
            print(f"An unexpected error occurred while parsing DHCP lease line '{line}': {e}")
    return router_ip, leases_data

# --- Data Storage ---
def store_hourly_traffic_data(db_file, router_ip, data):
    """
    Calculates the hourly traffic delta, stores it in the 'traffic_data' table,
    and updates the running total in the 'total_traffic' table.
    """
    if not data:
        print(f"No hourly traffic data to store for router {router_ip}.")
        return

    try:
        with sqlite3.connect(db_file) as conn:
            cursor = conn.cursor()
            current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for item in data:
                device_mac = item['device']
                current_rx = item['rx']
                current_tx = item['tx']

                cursor.execute("SELECT last_rx_bytes, last_tx_bytes FROM last_known_stats WHERE device_mac = ?", (device_mac,))
                last_known_stats = cursor.fetchone()
                hourly_rx_bytes = 0
                hourly_tx_bytes = 0

                if last_known_stats:
                    last_rx, last_tx = last_known_stats
                    if current_rx < last_rx or current_tx < last_tx:
                        print(f"Router reboot detected for device {device_mac}. Resetting hourly count for this cycle.")
                        hourly_rx_bytes = current_rx
                        hourly_tx_bytes = current_tx
                    else:
                        hourly_rx_bytes = current_rx - last_rx
                        hourly_tx_bytes = current_tx - last_tx
                else:
                    hourly_rx_bytes = current_rx
                    hourly_tx_bytes = current_tx

                cursor.execute('''
                    INSERT INTO traffic_data (timestamp, device_mac, hourly_rx_bytes, hourly_tx_bytes)
                    VALUES (?, ?, ?, ?)
                ''', (current_timestamp, device_mac, hourly_rx_bytes, hourly_tx_bytes))

                cursor.execute("SELECT total_rx_bytes, total_tx_bytes FROM total_traffic WHERE device_mac = ?", (device_mac,))
                total_stats = cursor.fetchone()

                if total_stats:
                    new_total_rx = total_stats[0] + hourly_rx_bytes
                    new_total_tx = total_stats[1] + hourly_tx_bytes
                    cursor.execute('''
                        UPDATE total_traffic
                        SET total_rx_bytes = ?, total_tx_bytes = ?
                        WHERE device_mac = ?
                    ''', (new_total_rx, new_total_tx, device_mac))
                else:
                    cursor.execute('''
                        INSERT INTO total_traffic (device_mac, total_rx_bytes, total_tx_bytes)
                        VALUES (?, ?, ?)
                    ''', (device_mac, hourly_rx_bytes, hourly_tx_bytes))
                
                cursor.execute('''
                    INSERT OR REPLACE INTO last_known_stats (device_mac, last_rx_bytes, last_tx_bytes)
                    VALUES (?, ?, ?)
                ''', (device_mac, current_rx, current_tx))

            conn.commit()
            print(f"Successfully processed and stored hourly traffic data for router {router_ip} for {current_timestamp}.")
    except sqlite3.Error as e:
        print(f"Database error during hourly traffic data storage for {router_ip}: {e}")

def store_dhcp_leases(db_file, router_ip, leases_data):
    """
    Stores the parsed DHCP lease data into the SQLite database.
    """
    if not leases_data:
        print(f"No DHCP lease data to store for router {router_ip}.")
        return

    try:
        with sqlite3.connect(db_file) as conn:
            cursor = conn.cursor()
            current_collection_timestamp = datetime.now().strftime("%Y-%m-%d")
            data_to_store = [(current_collection_timestamp, router_ip, item['mac_address'], item['ip_address'], item['hostname']) for item in leases_data]
            cursor.executemany('''
                INSERT OR IGNORE INTO dhcp_leases (collection_timestamp, router_ip, mac_address, ip_address, hostname)
                VALUES (?, ?, ?, ?, ?)
            ''', data_to_store)
            conn.commit()
            print(f"Attempted to store DHCP lease data for router {router_ip}. Only new devices were added.")
    except sqlite3.Error as e:
        print(f"Database error during DHCP lease storage for {router_ip}: {e}")

# --- Data Cleanup ---
def cleanup_monthly_data(db_file):
    """
    Deletes all data from the 'traffic_data' that is from the previous month.
    """
    try:
        with sqlite3.connect(db_file) as conn:
            cursor = conn.cursor()
            first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")
            cursor.execute('''
                DELETE FROM traffic_data
                WHERE timestamp < ?
            ''', (first_day_of_month,))
            print(f"Deleted traffic data older than {first_day_of_month}.")
            conn.commit()
    except sqlite3.Error as e:
        print(f"Database error during monthly data cleanup: {e}")

# --- Main Execution ---
def main():
    """
    Main function to orchestrate fetching and storing OpenWrt traffic and DHCP lease data.
    """
    config = load_config()
    if not config:
        return

    db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), config['DATABASE_FILE'])
    setup_database(db_file)

    # --- Hourly Task: Fetch and store traffic data ---
    print("Running hourly traffic data collection...")
    for url in config['ROUTER_URLS']:
        router_ip, data = fetch_and_parse_data(url)
        if router_ip and data:
            store_hourly_traffic_data(db_file, router_ip, data)
        else:
            print(f"Skipping traffic data storage for {url} due to fetch/parse errors.")

    # --- Daily Tasks: Check if it's time to run ---
    current_time = datetime.now()
    if current_time.day == 1 and current_time.hour == config['DAILY_RUN_HOUR']:
        print(f"\nRunning monthly traffic data cleanup at {current_time.strftime('%H:%M')} on the first day of the month...")
        cleanup_monthly_data(db_file)
        
    if current_time.hour == config['DAILY_RUN_HOUR']:
        print(f"\nRunning daily DHCP lease collection at {current_time.strftime('%H:%M')}...")
        for url in config['DHCP_LEASE_URLS']:
            router_ip, leases_data = fetch_and_parse_dhcp_leases(url)
            if router_ip and leases_data:
                store_dhcp_leases(db_file, router_ip, leases_data)
            else:
                print(f"Skipping DHCP lease data storage for {url} due to fetch/parse errors.")
    else:
        print(f"\nDaily tasks (DHCP collection and monthly cleanup) are scheduled for {config['DAILY_RUN_HOUR']}:00. Skipping for now.")

    print("\nData collection process completed.")
    print(f"Data is stored in '{db_file}'.")
    print("This script is ready to be scheduled hourly via a systemd timer.")

if __name__ == "__main__":
    main()

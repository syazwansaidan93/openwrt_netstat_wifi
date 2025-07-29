import requests
import sqlite3
from datetime import datetime, timedelta # Import timedelta
import os
import re # Import regex module for MAC/IP detection

# --- Configuration ---
# List of OpenWrt router URLs to fetch traffic data from
ROUTER_URLS = [
    "http://192.168.1.1/cgi-bin/totalwifi.cgi",
    "http://192.168.1.2/cgi-bin/totalwifi.cgi",
]

# List of OpenWrt router URLs to fetch DHCP lease data from
# IMPORTANT: This assumes your /cgi-bin/dhcp.cgi script outputs data
# in the format you provided (e.g., timestamp mac_or_num ip hostname client_id)
# Only fetching from 192.168.1.1 as 192.168.1.2 acts as an AP and does not serve DHCP.
DHCP_LEASE_URLS = [
    "http://192.168.1.1/cgi-bin/dhcp.cgi",
]

# Path to the SQLite database file. It will be created in the same directory as the script.
DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openwrt_traffic.db")

# Number of days to keep data. Data older than this will be deleted.
# For traffic_data: retains daily snapshots for this many days.
# For dhcp_leases: retains records of devices first seen within this many days.
# Set to None or 0 to keep all data indefinitely.
DATA_RETENTION_DAYS = 30 # Keep data for 30 days

# --- Database Setup ---
def setup_database():
    """
    Connects to the SQLite database and creates the 'traffic_data' table
    if it doesn't already exist, without any migration logic.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # Create traffic_data table without 'router_ip' column
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS traffic_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device_mac TEXT NOT NULL,
                rx_bytes INTEGER NOT NULL,
                tx_bytes INTEGER NOT NULL,
                UNIQUE(timestamp, device_mac) ON CONFLICT REPLACE
            )
        ''')
        conn.commit()
        print(f"Database '{DATABASE_FILE}' and table 'traffic_data' are ready.")
    except sqlite3.Error as e:
        print(f"Database error during traffic_data setup: {e}")
    finally:
        if conn:
            conn.close()

def setup_dhcp_leases_table():
    """
    Connects to the SQLite database and creates the 'dhcp_leases' table
    if it doesn't already exist.
    This table will now store unique devices, with collection_timestamp as 'first seen' date.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dhcp_leases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_timestamp TEXT NOT NULL,
                router_ip TEXT NOT NULL,
                mac_address TEXT NOT NULL UNIQUE, -- Changed to UNIQUE(mac_address)
                ip_address TEXT NOT NULL,
                hostname TEXT
            )
        ''')
        # Note: If you previously ran the script, the UNIQUE constraint was on (collection_timestamp, router_ip, mac_address).
        # SQLite does not directly support altering UNIQUE constraints.
        # If you need to change this on an existing database, you would need to:
        # 1. Rename the old table.
        # 2. Create the new table with the desired UNIQUE(mac_address) constraint.
        # 3. Copy unique data from the old table to the new table.
        # 4. Drop the old table.
        # Since you haven't run the script yet, this new CREATE TABLE will be used from the start.

        conn.commit()
        print(f"Table 'dhcp_leases' is ready.")
    except sqlite3.Error as e:
        print(f"Database error during dhcp_leases setup: {e}")
    finally:
        if conn:
            conn.close()

# --- Data Fetching and Parsing ---
def fetch_and_parse_data(url):
    """
    Fetches traffic data from the given OpenWrt URL and parses it.

    Args:
        url (str): The URL of the totalwifi.cgi endpoint.

    Returns:
        tuple: A tuple containing (router_ip, list_of_device_data).
               list_of_device_data is a list of dictionaries, e.g.,
               [{'device': '08:38:e6:33:15:25', 'rx': 349550159, 'tx': 10783656796}, ...]
               Returns (None, None) if fetching or parsing fails.
    """
    try:
        # Extract router IP from the URL for logging/context if needed
        router_ip = url.split('//')[1].split('/')[0]

        print(f"Fetching traffic data from {url}...")
        response = requests.get(url, timeout=10) # 10-second timeout
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)

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
        return router_ip, parsed_data # router_ip is still returned for context, but not stored in DB
    except requests.exceptions.RequestException as e:
        print(f"Error fetching traffic data from {url}: {e}")
        return None, None
    except Exception as e:
        print(f"An unexpected error occurred while processing traffic data from {url}: {e}")
        return None, None

def fetch_and_parse_dhcp_leases(url):
    """
    Fetches DHCP lease data from the given OpenWrt URL and parses it
    based on the dhcp.cgi output format.

    Args:
        url (str): The URL of the dhcp.cgi endpoint.

    Returns:
        tuple: A tuple containing (router_ip, list_of_lease_data).
               list_of_lease_data is a list of dictionaries, e.g.,
               [{'mac_address': '00:11:22:33:44:55', 'ip_address': '192.168.1.100', 'hostname': 'mydevice'}, ...]
               Returns (None, None) if fetching or parsing fails.
    """
    try:
        router_ip = url.split('//')[1].split('/')[0]
        print(f"Fetching DHCP leases from {url}...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        leases_data = []
        # Regex to check if a string is likely a MAC address (XX:XX:XX:XX:XX:XX)
        mac_regex = re.compile(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')
        # Regex to check if a string is likely an IPv4 address
        ipv4_regex = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')
        # Regex to check if a string is likely an IPv6 address (simplified)
        ipv6_regex = re.compile(r'^[0-9a-fA-F:]+$') # More complex regex needed for full validation, but this is a quick check

        for line in response.text.strip().split('\n'):
            parts = line.split()

            if not parts: # Skip empty lines
                continue
            if parts[0] == 'duid': # Skip DUID lines
                continue
            if len(parts) < 4: # Minimum expected parts for a lease entry
                print(f"Warning: Skipping malformed DHCP lease line (too few parts): {line}")
                continue

            try:
                # lease_expiry_ts = parts[0].strip() # This is no longer needed for storage
                # The second part can be MAC or a number (for IPv6 leases)
                second_part = parts[1].strip()
                ip_address = parts[2].strip()
                hostname = parts[3].strip() if parts[3] != '*' else None

                unique_identifier = None # This will be MAC for IPv4, DUID for IPv6

                # Heuristic to determine if it's an IPv4 or IPv6 lease line
                if mac_regex.match(second_part): # Looks like a MAC address
                    unique_identifier = second_part # This is the MAC address
                    # For IPv4, client_id is usually parts[4] if present
                elif ipv4_regex.match(ip_address) or ipv6_regex.match(ip_address):
                    # If second_part is not a MAC, it's likely an IPv6 lease where second_part is a number
                    # and the actual unique identifier (DUID/client_id) is in parts[4] or later.
                    # We'll try to get the DUID from the last part if available.
                    if len(parts) >= 5: # Check if client_id/DUID part exists
                        unique_identifier = parts[-1].strip() # Use the last part as the unique identifier (DUID)
                    else:
                        print(f"Warning: Could not determine unique identifier for lease: {line}. Skipping.")
                        continue
                else:
                    print(f"Warning: Unrecognized lease format: {line}. Skipping.")
                    continue

                if unique_identifier:
                    leases_data.append({
                        'mac_address': unique_identifier, # Store MAC or DUID here
                        'ip_address': ip_address,
                        'hostname': hostname,
                        # 'lease_expiry_timestamp': lease_expiry_ts # This is no longer added to the dictionary
                    })

            except (ValueError, IndexError) as e:
                print(f"Warning: Error parsing DHCP lease line '{line}': {e}")
            except Exception as e:
                print(f"An unexpected error occurred while parsing DHCP lease line '{line}': {e}")

        return router_ip, leases_data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching DHCP leases from {url}: {e}")
        return None, None
    except Exception as e:
        print(f"An unexpected error occurred while processing DHCP leases from {url}: {e}")
        return None, None

# --- Data Storage ---
def store_data(router_ip, data):
    """
    Stores the parsed traffic data into the SQLite database.

    Args:
        router_ip (str): The IP address of the router (used for context, not stored in DB).
        data (list): A list of dictionaries containing device traffic data.
    """
    if not data:
        print(f"No traffic data to store for router {router_ip}.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        current_timestamp = datetime.now().strftime("%Y-%m-%d") # Store as YYYY-MM-DD

        for item in data:
            # Insert data without router_ip
            cursor.execute('''
                INSERT OR REPLACE INTO traffic_data (timestamp, device_mac, rx_bytes, tx_bytes)
                VALUES (?, ?, ?, ?)
            ''', (current_timestamp, item['device'], item['rx'], item['tx']))
        conn.commit()
        print(f"Successfully stored traffic data for router {router_ip} for {current_timestamp}.")
    except sqlite3.Error as e:
        print(f"Database error during traffic data storage for {router_ip}: {e}")
    finally:
        if conn:
            conn.close()

def store_dhcp_leases(router_ip, leases_data):
    """
    Stores the parsed DHCP lease data into the SQLite database.
    Only new devices (based on mac_address) will be inserted.
    """
    if not leases_data:
        print(f"No DHCP lease data to store for router {router_ip}.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        current_collection_timestamp = datetime.now().strftime("%Y-%m-%d")

        for item in leases_data:
            # Use INSERT OR IGNORE to only insert if mac_address is new
            # The collection_timestamp will reflect the first time the device was seen.
            cursor.execute('''
                INSERT OR IGNORE INTO dhcp_leases (collection_timestamp, router_ip, mac_address, ip_address, hostname)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                current_collection_timestamp,
                router_ip,
                item['mac_address'],
                item['ip_address'],
                item['hostname']
            ))
        conn.commit()
        print(f"Attempted to store DHCP lease data for router {router_ip} for {current_collection_timestamp}. Only new devices were added.")
    except sqlite3.Error as e:
        print(f"Database error during DHCP lease storage for {router_ip}: {e}")
    finally:
        if conn:
            conn.close()

# --- Data Cleanup ---
def cleanup_old_data(days_to_keep):
    """
    Deletes data from the 'traffic_data' table that is older than
    the specified number of days.

    Args:
        days_to_keep (int): The number of days of data to retain.
    """
    if days_to_keep is None or days_to_keep <= 0:
        print("Traffic data retention set to indefinite or invalid. Skipping cleanup.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # Calculate the cutoff date
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")

        cursor.execute('''
            DELETE FROM traffic_data
            WHERE timestamp < ?
        ''', (cutoff_date,))
        conn.commit()
        print(f"Deleted traffic data older than {cutoff_date} (retaining {days_to_keep} days).")
    except sqlite3.Error as e:
        print(f"Database error during traffic data cleanup: {e}")
    finally:
        if conn:
            conn.close()

def cleanup_old_dhcp_leases(days_to_keep):
    """
    Deletes data from the 'dhcp_leases' table that is older than
    the specified number of days.
    Note: For dhcp_leases, 'collection_timestamp' now represents the 'first seen' date.
    """
    if days_to_keep is None or days_to_keep <= 0:
        print("DHCP lease data retention set to indefinite or invalid. Skipping cleanup.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")

        cursor.execute('''
            DELETE FROM dhcp_leases
            WHERE collection_timestamp < ?
        ''', (cutoff_date,))
        conn.commit()
        print(f"Deleted DHCP lease data older than {cutoff_date} (retaining {days_to_keep} days).")
    except sqlite3.Error as e:
        print(f"Database error during DHCP lease cleanup: {e}")
    finally:
        if conn:
            conn.close()

# --- Main Execution ---
def main():
    """
    Main function to orchestrate fetching and storing OpenWrt traffic and DHCP lease data.
    """
    setup_database() # Setup for traffic_data table
    setup_dhcp_leases_table() # Setup for dhcp_leases table

    # Fetch and store traffic data
    for url in ROUTER_URLS:
        router_ip, data = fetch_and_parse_data(url)
        if router_ip and data:
            store_data(router_ip, data)
        else:
            print(f"Skipping traffic data storage for {url} due to fetch/parse errors.")

    # Fetch and store DHCP lease data
    for url in DHCP_LEASE_URLS:
        router_ip, leases_data = fetch_and_parse_dhcp_leases(url)
        if router_ip and leases_data:
            store_dhcp_leases(router_ip, leases_data)
        else:
            print(f"Skipping DHCP lease data storage for {url} due to fetch/parse errors.")

    # Perform data cleanup if DATA_RETENTION_DAYS is set
    if DATA_RETENTION_DAYS is not None and DATA_RETENTION_DAYS > 0:
        cleanup_old_data(DATA_RETENTION_DAYS) # Cleanup for traffic_data
        cleanup_old_dhcp_leases(DATA_RETENTION_DAYS) # Cleanup for dhcp_leases

    print("\nData collection process completed.")
    print(f"Data is stored in '{DATABASE_FILE}'.")
    print("You can run this script daily to collect traffic and DHCP lease data over time.")

if __name__ == "__main__":
    main()


import pyvisa
import sys

def query_scope(ip):
    rm = pyvisa.ResourceManager()
    # Using TCPIP::{ip}::INSTR as a generic resource string
    resource_str = f"TCPIP::{ip}::INSTR"
    print(f"Connecting to {resource_str}...")
    
    try:
        scope = rm.open_resource(resource_str)
        scope.timeout = 5000
        
        # Identification query
        idn = scope.query("*IDN?").strip()
        print(f"IDN: {idn}")
        
        # Extract Serial Number (usually the 3rd field in IDN response)
        parts = idn.split(',')
        if len(parts) >= 3:
            serial = parts[2].strip()
            print(f"Serial Number: {serial}")
        
        import subprocess
        import re
        mac_found = False
        #print("Attempting to retrieve MAC address via ARP...")
        try:
            # Ping the IP to ensure it's in the ARP table
            ping_cmd = ["ping", "-n", "1", ip] if sys.platform == "win32" else ["ping", "-c", "1", ip]
            subprocess.run(ping_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            arp_output = subprocess.check_output(["arp", "-a"]).decode("utf-8", errors="ignore")
            for line in arp_output.splitlines():
                if re.search(rf'\b{re.escape(ip)}\b', line):
                    mac_match = re.search(r'([0-9a-fA-F]{2}[:-]){5}([0-9a-fA-F]{2})', line)
                    if mac_match:
                        print(f"MAC Address: {mac_match.group(0).upper().replace('-', ':')}")
                        mac_found = True
                        break
            if not mac_found:
                print("Failed to find MAC address in ARP table.")
        except Exception as e:
            print(f"Error executing ping/arp: {e}")
            
        scope.close()
    except Exception as e:
        print(f"Error connecting to or querying scope at {ip}:")
        print(f"  {e}")
    finally:
        rm.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python query_scope_id.py <ip_address>")
        sys.exit(1)
    query_scope(sys.argv[1])

import time


def get_local_ip():
    import socket
    lan_ip = (([ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                if not ip.startswith("127.")]
               or [[(s.connect(("8.8.8.8", 53)), s.getsockname()[0], s.close())
                    for s in [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]])
              + ["no IP found"])[0]
    return lan_ip


# Crude test for whether IP address is a valid IPv4 private address
def is_private_ipv4_addr(ip_addr):
    ip_parts = ip_addr.split(".")
    if len(ip_parts) == 4:
        if ip_parts[0] == "192" and ip_parts[1] == "168":
            return True
        elif ip_parts[0] == "10":
            return True
        elif ip_parts[0] == "172":
            try:
                ip_part1 = int(ip_parts[1])
                if 16 <= ip_part1 <= 31:
                    return True
            except ValueError as e: # not valid int
                pass
    return False


def check():
    try:
        ips = get_local_ip()
        if isinstance(ips, str):
            if is_private_ipv4_addr(ips):
                return True
        else:
            for ip in ips:
                if is_private_ipv4_addr(ip):
                    return True
        return False
    except:
        return False


while not check():
    time.sleep(1)

import time
import asyncio
import threading
import click
import sys
import os
# Python imports will be the end of us all
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir))

from raccoon_src.utils.coloring import COLOR, COLORED_COMBOS
from raccoon_src.utils.exceptions import RaccoonException
from raccoon_src.utils.request_handler import RequestHandler
from raccoon_src.utils.logger import SystemOutLogger
from raccoon_src.utils.help_utils import HelpUtilities
from raccoon_src.lib.fuzzer import URLFuzzer
from raccoon_src.lib.host import Host
from raccoon_src.lib.scanner import Scanner, NmapScan
from raccoon_src.lib.sub_domain import SubDomainEnumerator
from raccoon_src.lib.dns_handler import DNSHandler
from raccoon_src.lib.waf import WAF
from raccoon_src.lib.tls import TLSHandler
from raccoon_src.lib.web_app import WebApplicationScanner

# Set path for relative access to builtin files.
MY_PATH = os.path.abspath(os.path.dirname(__file__))


def intro(logger):
    logger.info("""{}
 _____                _____    _____    ____     ____    _   _ 
|  __ \      /\      / ____|  / ____|  / __ \   / __ \  | \ | |
| |__) |    /  \    | |      | |      | |  | | | |  | | |  \| |
|  _  /    / /\ \   | |      | |      | |  | | | |  | | | . ` |
| | \ \   / ____ \  | |____  | |____  | |__| | | |__| | | |\  |
|_|  \_\ /_/    \_\  \_____|  \_____|  \____/   \____/  |_| \_|
{}

4841434b544845504c414e4554
    
https://github.com/evyatarmeged/Raccoon
-------------------------------------------------------------------
    """.format(COLOR.GRAY, COLOR.RESET))


@click.command()
@click.version_option("0.0.75")
@click.option("-t", "--target", required=True, help="Target to scan")
@click.option("-d", "--dns-records", default="A,MX,NS,CNAME,SOA,TXT",
              help="Comma separated DNS records to query. Defaults to: A,MX,NS,CNAME,SOA,TXT")
@click.option("--tor-routing", is_flag=True, help="Route HTTP traffic through Tor (uses port 9050)."
                                                  " Slows total runtime significantly")
@click.option("--proxy-list", help="Path to proxy list file that would be used for routing HTTP traffic."
                                   " A proxy from the list will be chosen at random for each request."
                                   " Slows total runtime")
@click.option("--proxy", help="Proxy address to route HTTP traffic through. Slows total runtime")
@click.option("-w", "--wordlist", default=os.path.join(MY_PATH, "wordlists/fuzzlist"),
              help="Path to wordlist that would be used for URL fuzzing")
@click.option("-T", "--threads", default=25,
              help="Number of threads to use for URL Fuzzing/Subdomain enumeration. Default: 25")
@click.option("--ignored-response-codes", default="302,400,401,402,403,404,503,504",
              help="Comma separated list of HTTP status code to ignore for fuzzing."
                   " Defaults to: 302,400,401,402,403,404,503,504")
@click.option("--subdomain-list", default=os.path.join(MY_PATH, "wordlists/subdomains"),
              help="Path to subdomain list file that would be used for enumeration")
@click.option("-S", "--scripts", is_flag=True, help="Run Nmap scan with -sC flag")
@click.option("-s", "--services", is_flag=True, help="Run Nmap scan with -sV flag")
@click.option("-f", "--full-scan", is_flag=True, help="Run Nmap scan with both -sV and -sC")
@click.option("-p", "--port", help="Use this port range for Nmap scan instead of the default")
@click.option("--tls-port", default=443, help="Use this port for TLS queries. Default: 443")
@click.option("--skip-health-check", is_flag=True, help="Do not test for target host availability")
@click.option("--follow-redirects", is_flag=True, default=False,
              help="Follow redirects when fuzzing. Default: False (will not follow redirects)")
@click.option("--no-url-fuzzing", is_flag=True, help="Do not fuzz URLs")
@click.option("--no-sub-enum", is_flag=True, help="Do not bruteforce subdomains")
@click.option("--skip-nmap-scan", is_flag=True, help="Do not perform an Nmap scan")
# @click.option("-d", "--delay", default="0.25-1",
#               help="Min and Max number of seconds of delay to be waited between requests\n"
#                    "Defaults to Min: 0.25, Max: 1. Specified in the format of Min-Max")
@click.option("-q", "--quiet", is_flag=True, help="Do not output to stdout")
@click.option("-o", "--outdir", default="Raccoon_scan_results",
              help="Directory destination for scan output")
def main(target,
         tor_routing,
         proxy_list,
         proxy,
         dns_records,
         wordlist,
         threads,
         ignored_response_codes,
         subdomain_list,
         full_scan,
         scripts,
         services,
         port,
         tls_port,
         skip_health_check,
         follow_redirects,
         no_url_fuzzing,
         no_sub_enum,
         skip_nmap_scan,
         # delay,
         outdir,
         quiet):
    try:
        # ------ Arg validation ------

        # Set logging level and Logger instance
        log_level = HelpUtilities.determine_verbosity(quiet)
        logger = SystemOutLogger(log_level)
        intro(logger)

        target = target.lower()
        try:
            HelpUtilities.validate_executables()
        except RaccoonException as e:
            logger.critical(str(e))
            exit(9)
        HelpUtilities.validate_wordlist_args(proxy_list, wordlist, subdomain_list)
        HelpUtilities.validate_proxy_args(tor_routing, proxy, proxy_list)
        HelpUtilities.create_output_directory(outdir)

        if tor_routing:
            logger.info("{} Testing that Tor service is up...".format(COLORED_COMBOS.NOTIFY))
        elif proxy_list:
            if proxy_list and not os.path.isfile(proxy_list):
                raise FileNotFoundError("Not a valid file path, {}".format(proxy_list))
            else:
                logger.info("{} Routing traffic using proxies from list {}\n".format(
                    COLORED_COMBOS.NOTIFY, proxy_list))
        elif proxy:
            logger.info("{} Routing traffic through proxy {}\n".format(COLORED_COMBOS.NOTIFY, proxy))

        # TODO: Sanitize delay argument

        dns_records = tuple(dns_records.split(","))
        ignored_response_codes = tuple(int(code) for code in ignored_response_codes.split(","))

        if port:
            HelpUtilities.validate_port_range(port)

        # ------ /Arg validation ------

        # Set Request Handler instance
        request_handler = RequestHandler(proxy_list=proxy_list, tor_routing=tor_routing, single_proxy=proxy)

        if tor_routing:
            try:
                HelpUtilities.confirm_traffic_routs_through_tor()
                logger.info("{} Validated Tor service is up. Routing traffic anonymously\n".format(
                    COLORED_COMBOS.NOTIFY))
            except RaccoonException as err:
                print("{}{}{}".format(COLOR.RED, str(err), COLOR.RESET))
                exit(3)

        main_loop = asyncio.get_event_loop()

        logger.info("{}### Raccoon Scan Started ###{}\n".format(COLOR.GRAY, COLOR.RESET))
        logger.info("{} Trying to gather information about host: {}".format(COLORED_COMBOS.INFO, target))

        # TODO: Populate array when multiple targets are supported
        # hosts = []
        host = Host(target=target, dns_records=dns_records)
        host.parse()

        if not skip_health_check:
            try:
                HelpUtilities.validate_target_is_up(host)
            except RaccoonException as err:
                logger.critical("{}{}{}".format(COLOR.RED, str(err), COLOR.RESET))
                exit(42)

        if not skip_nmap_scan:
            logger.info("\n{} Setting Nmap scan to run in the background".format(COLORED_COMBOS.INFO))
            nmap_scan = NmapScan(host, full_scan, scripts, services, port)
            # # # TODO: Populate array when multiple targets are supported
            # nmap_threads = []
            nmap_thread = threading.Thread(target=Scanner.run, args=(nmap_scan,))
            # Run Nmap scan in the background. Can take some time
            nmap_thread.start()

        # Run first set of checks - TLS, Web/WAF Data, DNS data
        waf = WAF(host)
        tls_info_scanner = TLSHandler(host, tls_port)
        web_app_scanner = WebApplicationScanner(host)
        tasks = (
            asyncio.ensure_future(tls_info_scanner.run()),
            asyncio.ensure_future(waf.detect()),
            asyncio.ensure_future(DNSHandler.grab_whois(host)),
            asyncio.ensure_future(web_app_scanner.run_scan()),
            asyncio.ensure_future(DNSHandler.generate_dns_dumpster_mapping(host, logger))
        )

        main_loop.run_until_complete(asyncio.wait(tasks))

        # Second set of checks - URL fuzzing, Subdomain enumeration
        if not no_url_fuzzing:
            fuzzer = URLFuzzer(host, ignored_response_codes, threads, wordlist, follow_redirects)
            main_loop.run_until_complete(fuzzer.fuzz_all())

        if not host.is_ip:
            sans = tls_info_scanner.sni_data.get("SANs")
            subdomain_enumerator = SubDomainEnumerator(
                host,
                domain_list=subdomain_list,
                sans=sans,
                ignored_response_codes=ignored_response_codes,
                num_threads=threads,
                follow_redirects=follow_redirects,
                no_sub_enum=no_sub_enum
            )
            main_loop.run_until_complete(subdomain_enumerator.run())

        if not skip_nmap_scan:
            if nmap_thread.is_alive():
                logger.info("{} All scans done. Waiting for Nmap scan to wrap up. "
                            "Time left may vary depending on scan type and port range".format(COLORED_COMBOS.INFO))

                while nmap_thread.is_alive():
                    time.sleep(15)

        logger.info("\n{}### Raccoon scan finished ###{}\n".format(COLOR.GRAY, COLOR.RESET))
        os.system("stty sane")

    except KeyboardInterrupt:
        print("{}Keyboard Interrupt detected. Exiting{}".format(COLOR.RED, COLOR.RESET))
        # Fix F'd up terminal after CTRL+C
        os.system("stty sane")
        exit(42)


if __name__ == "__main__":
    main()

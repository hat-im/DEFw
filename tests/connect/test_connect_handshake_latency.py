#!/usr/bin/env python3
import os
import signal
import socket
import subprocess
import sys
import time

RUNS = 3
CONNECT_BOUND_SEC = 0.5
START_TIMEOUT_SEC = 30


def expect(condition, message):
	if not condition:
		raise AssertionError(message)


def free_port():
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
		sock.bind(('127.0.0.1', 0))
		return sock.getsockname()[1]


def base_env(name, listen_port, log_dir):
	env = dict(os.environ)
	env.update({
		'PYTHONDONTWRITEBYTECODE': '1',
		'DEFW_AGENT_NAME': name,
		'DEFW_LISTEN_PORT': str(listen_port),
		'DEFW_TELNET_PORT': str(free_port()),
		'DEFW_LOG_LEVEL': 'error',
		'DEFW_LOG_DIR': log_dir,
		'DEFW_EXTERNAL_SERVICES_PATH': '',
		'DEFW_EXTERNAL_SERVICE_APIS_PATH': '',
		'DEFW_EXTERNAL_EXPERIMENTS_PATH': '',
	})
	if os.environ.get('DEFW_TEST_CONFIG_PATH'):
		env['DEFW_CONFIG_PATH'] = os.environ['DEFW_TEST_CONFIG_PATH']
	os.makedirs(log_dir, exist_ok=True)
	return env


def start_directory(defwp, port, log_dir):
	env = base_env('latency-dirsvc', port, log_dir)
	env.update({
		'DEFW_ONLY_LOAD_MODULE': 'svc_dirsvc',
		'DEFW_LOAD_NO_INIT': '',
		'DEFW_SHELL_TYPE': 'daemon',
		'DEFW_AGENT_TYPE': 'dirsvc',
		'DEFW_PARENT_HOSTNAME': '127.0.0.1',
		'DEFW_PARENT_ADDR': '127.0.0.1',
		'DEFW_PARENT_PORT': str(port),
		'DEFW_PARENT_NAME': 'latency-dirsvc',
		'DEFW_DISABLE_DIRSVC': 'yes',
	})
	launcher = subprocess.run([defwp, '-d'], env=env,
				  stdout=subprocess.DEVNULL,
				  stderr=subprocess.DEVNULL, timeout=60)
	expect(launcher.returncode == 0,
	       f"directory service launch failed with {launcher.returncode}")
	deadline = time.time() + START_TIMEOUT_SEC
	while time.time() < deadline:
		try:
			socket.create_connection(('127.0.0.1', port), 0.2).close()
			return int(open(os.path.join(log_dir, 'pid')).read().split()[0])
		except (OSError, ValueError, IndexError):
			time.sleep(0.1)
	raise AssertionError("directory service never listened")


CONNECTOR = """
import time
import defw
start = time.time()
defw.wait_dirsvc(10)
print(f'connect seconds {time.time() - start:.3f}', flush=True)
"""


def connect_seconds(defwp, target_port, log_dir):
	env = base_env('latency-connector', free_port(), log_dir)
	env.update({
		'DEFW_SHELL_TYPE': 'cmdline',
		'DEFW_AGENT_TYPE': 'agent',
		'DEFW_PARENT_NAME': 'latency-dirsvc',
		'DEFW_PARENT_HOSTNAME': '127.0.0.1',
		'DEFW_PARENT_ADDR': '127.0.0.1',
		'DEFW_PARENT_PORT': str(target_port),
		'DEFW_DISABLE_DIRSVC': 'no',
	})
	proc = subprocess.run([defwp, '-c', CONNECTOR], env=env,
			      capture_output=True, text=True, timeout=60)
	output = proc.stdout + proc.stderr
	for line in proc.stdout.splitlines():
		if line.startswith('connect seconds '):
			return float(line.split()[-1])
	raise AssertionError(f"connector failed (rc {proc.returncode}):\n{output}")


def main():
	defwp = os.environ['DEFW_TEST_DEFWP']
	log_dir = os.environ.get('DEFW_TEST_LOG_DIR', '/tmp/defw-latency-log')
	port = free_port()
	directory = start_directory(defwp, port, os.path.join(log_dir, 'dirsvc'))
	try:
		seconds = [connect_seconds(defwp, port,
					   os.path.join(log_dir, f'connector-{run}'))
			   for run in range(RUNS)]
	finally:
		os.kill(directory, signal.SIGKILL)
	print('connect seconds:', ', '.join(f'{s:.3f}' for s in seconds))
	expect(max(seconds) < CONNECT_BOUND_SEC,
	       f"a connect took {max(seconds):.3f}s, the listener waited out "
	       "its select() timeout")
	print('a new connection completes without the select() timeout: ok')
	return 0


if __name__ == '__main__':
	sys.exit(main())

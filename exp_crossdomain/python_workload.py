"""Long-running Python process whose memory we monitor.
Sleeps, then performs an action on signal, then sleeps again.
We use SIGUSR1 to trigger the action so the parent (memdump runner)
can drive timing precisely.
"""
import os, sys, time, signal, gc
ACTION = os.environ.get("ACTION_KIND", "python_dict_10")
gc.disable()  # we want allocator activity to be observable, not GC'd away
# Pre-warm: allocate a baseline object set so heap isn't a single page
warmup_data = []
for _ in range(200):
    warmup_data.append(bytes(1024))  # 200 x 1 KiB = 200 KiB baseline
sys.stdout.write(f"workload ready pid={os.getpid()}\n"); sys.stdout.flush()

action_done = False

def do_action(*_):
    global action_done
    if action_done:
        return
    action_done = True
    if ACTION == "python_dict_10":
        d = {k: k*2 for k in range(10)}
        sys.stdout.write(f"dict_10 len={len(d)}\n")
    elif ACTION == "python_dict_1k":
        d = {k: k*2 for k in range(1000)}
        sys.stdout.write(f"dict_1k len={len(d)}\n")
    elif ACTION == "python_list_100k":
        l = [i for i in range(100000)]
        l.sort(reverse=True)
        sys.stdout.write(f"list_100k len={len(l)}\n")
    sys.stdout.flush()
    # Don't free — let echo persist as long as possible
    globals()["_kept"] = locals()

signal.signal(signal.SIGUSR1, do_action)

while True:
    time.sleep(1)

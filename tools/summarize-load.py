#!/usr/bin/env python3
"""Summarise the load test's samples into one reading per family and tier.

The point of this script is to keep two sums apart, because adding them together
is the mistake this study keeps having to avoid. The engine's memory lives inside
the virtual machine on this host, so adding it to the virtual machine's resident
size counts the weights twice. What a device with 8 GB would actually hold is
the engine's own counter plus the harness plus the window, and that is the sum
reported as `device_resident_bytes`. What this host pays is the virtual machine
plus the window, reported as `host_visible_bytes`. Both are in the file, each is
named, and neither is offered as the other.
"""

import json
import sys

GIB = 1024 ** 3
FAMILIES = [
    ("engine_cgroup", "engine_cgroup_bytes", 1),
    ("engine_anon", "engine_anon_bytes", 1),
    ("engine_file_mapped", "engine_file_bytes", 1),
    ("engine_daemon", "engine_daemon_bytes", 1),
    ("harness_cgroup", "harness_cgroup_bytes", 1),
    ("window", "electron_kb", 1024),
    ("virtual_machine", "vm_kb", 1024),
]


def read(path):
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        header = handle.readline().strip().split(",")
        for line in handle:
            parts = line.strip().split(",")
            if len(parts) != len(header):
                continue
            row = {"tier": parts[0], "second": int(parts[1])}
            for name, value in zip(header[2:], parts[2:]):
                row[name] = int(value) if value not in ("", None) else None
            rows.append(row)
    return rows


def family_stats(rows, key, scale):
    values = [row[key] * scale for row in rows if row.get(key) is not None]
    if not values:
        return {"samples": 0, "absent": True, "peak_bytes": None, "mean_bytes": None, "last_bytes": None}
    return {
        "samples": len(values),
        "absent": False,
        "peak_bytes": max(values),
        "mean_bytes": int(round(sum(values) / len(values))),
        "last_bytes": values[-1],
    }


def tier_stats(rows):
    tiers = {}
    for row in rows:
        tiers.setdefault(row["tier"], []).append(row)
    out = {}
    for tier, tier_rows in tiers.items():
        out[tier] = {
            "seconds_sampled": len(tier_rows),
            "families": {label: family_stats(tier_rows, key, scale) for label, key, scale in FAMILIES},
        }
    return out


def peak(tiers, tier, family):
    entry = tiers.get(tier, {}).get("families", {}).get(family, {})
    return entry.get("peak_bytes")


def main():
    if len(sys.argv) != 4:
        print("usage: summarize-load.py <samples.csv> <out.json> <out.txt>", file=sys.stderr)
        return 2

    samples, json_path, text_path = sys.argv[1], sys.argv[2], sys.argv[3]
    rows = read(samples)
    tiers = tier_stats(rows)

    engine_off = peak(tiers, "t1-engine-vision-off", "engine_cgroup")
    engine_on = peak(tiers, "t5-engine-vision-on", "engine_cgroup")
    # The harness container is short lived, so its reading is taken from
    # whichever tier caught it alive. The tier is reported beside the figure,
    # because a number whose basis is a different condition is not the same
    # number and must not be presented as if it were.
    harness = None
    harness_basis = None
    for tier in ["t2-harness-run", "t4-window-and-run"]:
        value = peak(tiers, tier, "harness_cgroup")
        if value:
            harness = max(harness or 0, value)
            harness_basis = tier
    harness = harness or 0
    window = peak(tiers, "t3-window-idle", "window") or peak(tiers, "t4-window-and-run", "window") or 0
    vm = peak(tiers, "t1-engine-vision-off", "virtual_machine") or 0
    anon_off = peak(tiers, "t1-engine-vision-off", "engine_anon")
    anon_ctx = peak(tiers, "t6-engine-ctx-8192", "engine_anon")
    mapped = peak(tiers, "t1-engine-vision-off", "engine_file_mapped")

    projector_cost = None
    if engine_on is not None and engine_off is not None:
        projector_cost = engine_on - engine_off

    cache_cost = None
    if anon_ctx is not None and anon_off is not None:
        cache_cost = anon_ctx - anon_off

    device_total = sum(value for value in [engine_off, harness, window] if value is not None)
    device_total_vision = None
    if engine_on is not None:
        device_total_vision = engine_on + harness + window

    report = {
        "document": "agent-like-load-test",
        "samples_file": samples,
        "columns": {
            "note": "peak is the maximum over the samples of a tier, mean is the average, last is the reading at the end",
            "engine_anon": "memory the runtime allocated: the caches, the buffers and the process",
            "engine_file_mapped": "the memory mapped weights, which the kernel may reclaim under pressure",
            "engine_daemon": "what docker stats reported, kept as a cross-check of the cgroup counter",
            "virtual_machine": "the resident size of the lima and colima processes on the host. With the vz driver the guest's pages are not accounted in those processes, so this figure is the host process and not the guest, and the container counters above are the reading used",
        },
        "tiers": tiers,
        "reading": {
            "engine_with_vision_off_bytes": engine_off,
            "engine_with_vision_on_bytes": engine_on,
            "projector_cost_bytes": projector_cost,
            "engine_anonymous_bytes": anon_off,
            "engine_weights_mapped_bytes": mapped,
            "engine_cache_cost_for_4096_more_tokens_bytes": cache_cost,
            "harness_run_peak_bytes": harness if harness else None,
            "harness_run_basis_tier": harness_basis,
            "window_peak_bytes": window if window else None,
            "device_resident_bytes": device_total,
            "device_resident_with_vision_bytes": device_total_vision,
            "host_process_bytes": vm + window,
            "cap_bytes": 8 * GIB,
            "headroom_bytes": 8 * GIB - device_total,
            "fits_the_cap": device_total < 8 * GIB,
            "note": (
                "device_resident_bytes is the engine's own counter plus the harness plus the window, "
                "which is what a device running the engine natively would hold. The engine's figure is "
                "mostly page cache of the memory mapped weights, so the part of it that cannot be "
                "reclaimed is engine_anonymous_bytes. host_process_bytes is the virtual machine's "
                "process plus the window and is not a device figure at all: the vz driver keeps the "
                "guest's pages out of the host process, which is why this study reads the container "
                "counters."
            ),
        },
    }

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    def mib(value):
        return "not measured" if value is None else f"{value / (1024 ** 2):.1f} MiB"

    lines = [
        "Agent-Like load test",
        "",
        f"samples: {samples}",
        "",
        "tier,family,peak,mean,samples",
    ]
    for tier in sorted(tiers):
        for family in sorted(tiers[tier]["families"]):
            entry = tiers[tier]["families"][family]
            lines.append(
                f"{tier},{family},{mib(entry['peak_bytes'])},{mib(entry['mean_bytes'])},{entry['samples']}"
            )
    lines += [
        "",
        f"engine, vision off:      {mib(engine_off)}",
        f"  of which anonymous:    {mib(anon_off)}",
        f"  of which weights:      {mib(mapped)}",
        f"engine, vision on:       {mib(engine_on)}",
        f"projector cost:          {mib(projector_cost)}",
        f"cache, +4096 tokens:     {mib(cache_cost)}",
        f"harness run:             {mib(harness if harness else None)}",
        f"window:                  {mib(window if window else None)}",
        f"device resident:         {mib(device_total)} of 8 GiB, headroom {mib(8 * GIB - device_total)}",
        f"device with vision:      {mib(device_total_vision)}",
        f"host processes only:     {mib(vm + window)}",
    ]

    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())

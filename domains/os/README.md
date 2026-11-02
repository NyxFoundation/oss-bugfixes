# domains/os

**Scope rule.** A repository belongs here when it runs with a privilege the
applications above it **cannot revoke** — a kernel, a bootloader, a hypervisor, a
trusted execution environment, a C library, an init system. A defect here is a
defect in every program on the machine.

## Coverage — 35 repositories

| | |
|---|---|
| kernels | linux · linux-stable · freebsd · openbsd · netbsd · illumos · redox · seL4 |
| RTOS | zephyr · FreeRTOS · nuttx |
| hypervisors / isolation | xen · qemu · firecracker · cloud-hypervisor · gvisor · kata |
| firmware / boot | u-boot · coreboot · edk2 · grub · arm-trusted-firmware · OP-TEE · openenclave |
| libc / core userland | glibc · musl · systemd · busybox · sudo · openssh · dbus · util-linux |
| container runtimes | runc · containerd · crun |

Each entry carries a `privilege` tag: kernel 11 · userland 9 · hypervisor 6 ·
firmware 5 · tee 2 · libc 2. By tier: 9 / 15 / 11.

## Read this one as the calibration set

This is the domain where the method has the **least to prove and the most to
learn from**. The Linux kernel has been mined for silent fixes for years, and its
stable-tree backport discipline is the clearest real-world instance of the
`is_backport` signal meaning what this project claims it means: a patch
cherry-picked to a maintenance branch is a maintainer saying, by action rather
than in words, that users on the old release cannot wait.

So treat results here as a check on the harness, not as news about Linux. If the
backport signal and the classifier disagree with the kernel's own CVE record,
the harness is what is wrong.

## A warning about scale

`torvalds/linux` is roughly 1.4 million commits. The mirror alone takes tens of
minutes and several GB, and hydrating it takes considerably more. Start scoped:

```bash
oss-bugfixes mirror --domain os --repo sudo --repo openssh
oss-bugfixes walk   --domain os --repo sudo --limit 2000
```

## No migrated corpus

There is no predecessor dataset for this domain; `data/` fills up when you run
the pipeline.

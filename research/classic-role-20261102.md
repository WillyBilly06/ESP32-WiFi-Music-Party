# Bounded Classic role experiment

The identity-matched Linux hci1 reported itself CENTRAL on the COM6 ACL
(handle3), so ESP32 was the BR/EDR peripheral. Its AFH map read was
`81fffff0000000000060`; do not confuse the byte order with an integer mask.
No AFH map was changed. These observations do not establish RF airtime or
a cause of packet loss.

A normal hcitool role-switch request lacked permission. An authorized sudo
request on this same adapter returned I/O error and the role stayed CENTRAL
on Linux. The role change did not succeed.

Pinned BTstack defaults `master_slave_policy=1`. The new application candidate
requests central role on incoming Classic setup, enables standard role-switch
link policy, and reports the actual cached role plus role-change status.
`ROLEPOLICY1` restores peripheral preference for subsequent connections and
`ROLECENTRAL` requests a switch on the current admitted phone link. These
commands are serialized on the BTstack host; no raw VHCI injection competes
with the host. Hardware validation remains pending.

This is the BR/EDR piconet role, independently of Music Party leadership.
The hypothesis is that local central scheduling may improve RF coexistence;
it is not yet a demonstrated fix.

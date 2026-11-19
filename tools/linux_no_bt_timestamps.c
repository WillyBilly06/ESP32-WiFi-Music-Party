/* Diagnostic Linux-source workaround, never linked into ESP32 firmware.
 * PipeWire falls back to TIOCOUTQ when SO_TIMESTAMPING is unavailable.
 * Restrict this experiment to connected L2CAP sockets for admitted COM6.
 * Load only in the replacement source's WirePlumber user service.
 */
#define _GNU_SOURCE
#include <bluetooth/bluetooth.h>
#include <bluetooth/l2cap.h>
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

typedef int (*setsockopt_fn)(int, int, int, const void *, socklen_t);
static setsockopt_fn next_setsockopt;
static pthread_once_t resolve_once = PTHREAD_ONCE_INIT;

static void resolve_setsockopt(void)
{
    next_setsockopt = (setsockopt_fn)dlsym(RTLD_NEXT, "setsockopt");
}

int setsockopt(int fd, int level, int option, const void *value, socklen_t length)
{
    int protocol = -1, requested = 0;
    socklen_t protocol_length = sizeof(protocol);
    struct sockaddr_l2 peer = {0};
    socklen_t peer_length = sizeof(peer);
    /* bdaddr_t uses least-significant byte first: 6C:C8:40:42:BF:66. */
    static const unsigned char target[6] = {0x66, 0xbf, 0x42, 0x40, 0xc8, 0x6c};
    int timestamp_option = option == SO_TIMESTAMPING;
#if defined(SO_TIMESTAMPING_NEW) && SO_TIMESTAMPING_NEW != SO_TIMESTAMPING
    timestamp_option |= option == SO_TIMESTAMPING_NEW;
#endif
    pthread_once(&resolve_once, resolve_setsockopt);
    if (!next_setsockopt) {
        errno = ENOSYS;
        return -1;
    }
    if (value && length >= sizeof(requested))
        memcpy(&requested, value, sizeof(requested));
    if (level == SOL_SOCKET && timestamp_option && requested &&
        getsockopt(fd, SOL_SOCKET, SO_PROTOCOL, &protocol, &protocol_length) == 0 &&
        protocol == BTPROTO_L2CAP &&
        getpeername(fd, (struct sockaddr *)&peer, &peer_length) == 0 &&
        peer.l2_family == AF_BLUETOOTH &&
        memcmp(peer.l2_bdaddr.b, target, sizeof(target)) == 0) {
        static const char message[] =
            "wmp: disabled SO_TIMESTAMPING for admitted COM6 L2CAP socket\n";
        ssize_t written = write(STDERR_FILENO, message, sizeof(message) - 1);
        (void)written;
        errno = EOPNOTSUPP;
        return -1;
    }
    return next_setsockopt(fd, level, option, value, length);
}

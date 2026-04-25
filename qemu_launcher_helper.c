#include <dispatch/dispatch.h>
#include <err.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <vmnet/vmnet.h>

/*
 * QEMU Launcher Networking Helper (macOS)
 * This tool runs as SUID root to initialize vmnet.framework and passes
 * the resulting file descriptor to the user-mode QEMU process.
 */

#define SOCKET_PATH "/tmp/qemu-launcher-net.sock"

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <shared|bridged> [interface_name]\n", argv[0]);
        return 1;
    }

    // 1. Configure vmnet
    xpc_object_t interface_desc = xpc_dictionary_create(NULL, NULL, 0);
    if (strcmp(argv[1], "shared") == 0) {
        xpc_dictionary_set_uint64(interface_desc, vmnet_operation_mode_key, VMNET_SHARED_MODE);
    } else {
        xpc_dictionary_set_uint64(interface_desc, vmnet_operation_mode_key, VMNET_BRIDGED_MODE);
        if (argc > 2) {
            xpc_dictionary_set_string(interface_desc, vmnet_interface_name_key, argv[2]);
        }
    }

    __block int vmnet_fd = -1;
    __block vmnet_return_t status;
    dispatch_semaphore_t sema = dispatch_semaphore_create(0);

    interface_ref ref = vmnet_start_interface(interface_desc, dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_HIGH, 0), ^(vmnet_return_t sts, xpc_object_t interface_param) {
        status = sts;
        if (status == VMNET_SUCCESS) {
            vmnet_fd = vmnet_get_fd(ref);
        }
        dispatch_semaphore_signal(sema);
    });

    dispatch_semaphore_wait(sema, DISPATCH_TIME_FOREVER);

    if (status != VMNET_SUCCESS || vmnet_fd == -1) {
        fprintf(stderr, "Failed to start vmnet interface: %d\n", status);
        return 1;
    }

    // 2. Set up Unix socket to pass the FD
    unlink(SOCKET_PATH);
    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) == -1) {
        perror("bind");
        return 1;
    }

    if (listen(server_fd, 1) == -1) {
        perror("listen");
        return 1;
    }

    // Allow user to connect
    chmod(SOCKET_PATH, 0666);

    int client_fd = accept(server_fd, NULL, NULL);
    if (client_fd == -1) {
        perror("accept");
        return 1;
    }

    // 3. Pass the FD using SCM_RIGHTS
    struct msghdr msg = {0};
    char buf[CMSG_SPACE(sizeof(int))];
    memset(buf, 0, sizeof(buf));

    struct iovec io = { .iov_base = "FD", .iov_len = 2 };
    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    msg.msg_control = buf;
    msg.msg_controllen = sizeof(buf);

    struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    *((int *)CMSG_DATA(cmsg)) = vmnet_fd;

    if (sendmsg(client_fd, &msg, 0) == -1) {
        perror("sendmsg");
        return 1;
    }

    // Wait for client to close before exiting (keeps vmnet alive)
    char sync_buf[1];
    read(client_fd, sync_buf, 1);

    close(client_fd);
    close(server_fd);
    unlink(SOCKET_PATH);

    return 0;
}

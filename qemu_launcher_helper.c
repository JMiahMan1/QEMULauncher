#include <dispatch/dispatch.h>
#include <xpc/xpc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vmnet/vmnet.h>

#include <ctype.h>

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <shared|bridged> [interface_name] [socket_id]\n", argv[0]);
        return 1;
    }

    char socket_path[256];
    if (argc > 3 && strlen(argv[3]) > 0) {
        for(char *p = argv[3]; *p; p++) {
            if(!isalnum(*p) && *p != '-' && *p != '_') {
                fprintf(stderr, "Invalid socket_id\n");
                return 1;
            }
        }
        snprintf(socket_path, sizeof(socket_path), "/tmp/qemu-launcher-net-%s.sock", argv[3]);
    } else {
        snprintf(socket_path, sizeof(socket_path), "/tmp/qemu-launcher-net.sock");
    }

    xpc_object_t interface_desc = xpc_dictionary_create(NULL, NULL, 0);
    if (strcmp(argv[1], "shared") == 0) {
        xpc_dictionary_set_uint64(interface_desc, vmnet_operation_mode_key, VMNET_SHARED_MODE);
    } else {
        xpc_dictionary_set_uint64(interface_desc, vmnet_operation_mode_key, VMNET_BRIDGED_MODE);
        if (argc > 2 && strlen(argv[2]) > 0) {
            // This is the correct Apple key for both shared and bridged targeting
            xpc_dictionary_set_string(interface_desc, vmnet_shared_interface_name_key, argv[2]);
        }
    }

    dispatch_queue_t queue = dispatch_queue_create("qemu.launcher.vmnet", DISPATCH_QUEUE_SERIAL);
    __block interface_ref ref = NULL;
    dispatch_semaphore_t sema = dispatch_semaphore_create(0);

    ref = vmnet_start_interface(interface_desc, queue, ^(vmnet_return_t status, xpc_object_t interface_param) {
        if (status != VMNET_SUCCESS) {
            fprintf(stderr, "Failed to start vmnet interface: %d\n", status);
            exit(1);
        }
        dispatch_semaphore_signal(sema);
    });

    dispatch_semaphore_wait(sema, DISPATCH_TIME_FOREVER);

    // Setup Unix Socket Server for QEMU to connect to
    unlink(socket_path);
    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) == -1) exit(1);
    if (listen(server_fd, 1) == -1) exit(1);
    chmod(socket_path, 0666); // Crucial: Allow standard user QEMU to connect

    int client_fd = accept(server_fd, NULL, NULL);
    if (client_fd == -1) exit(1);

    // --- Packet Forwarding Loop ---
    // QEMU Protocol: uint32_t packet_len (host byte order) followed by raw packet.

    // 1. Read from QEMU, Write to vmnet
    dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_HIGH, 0), ^{
        while (1) {
            uint32_t len;
            if (recv(client_fd, &len, sizeof(len), MSG_WAITALL) != sizeof(len)) exit(0);
            
            char *buf = malloc(len);
            if (recv(client_fd, buf, len, MSG_WAITALL) != len) {
                free(buf);
                exit(0);
            }
            
            struct vmpktdesc pkt;
            pkt.vm_pkt_size = len;
            pkt.vm_pkt_iov = &(struct iovec){.iov_base = buf, .iov_len = len};
            pkt.vm_pkt_iovcnt = 1;
            pkt.vm_flags = 0;
            
            int pktcnt = 1;
            vmnet_write(ref, &pkt, &pktcnt);
            free(buf);
        }
    });

    // 2. Read from vmnet, Write to QEMU
    vmnet_interface_set_event_callback(ref, VMNET_INTERFACE_PACKETS_AVAILABLE, queue, ^(interface_event_t event_id, xpc_object_t event) {
        char buf[10000];
        struct vmpktdesc pkt;
        pkt.vm_pkt_size = sizeof(buf);
        pkt.vm_pkt_iov = &(struct iovec){.iov_base = buf, .iov_len = sizeof(buf)};
        pkt.vm_pkt_iovcnt = 1;
        pkt.vm_flags = 0;
        
        while (1) {
            int pktcnt = 1;
            if (vmnet_read(ref, &pkt, &pktcnt) != VMNET_SUCCESS || pktcnt == 0) {
                break;
            }
            uint32_t len = pkt.vm_pkt_size;
            send(client_fd, &len, sizeof(len), 0);
            send(client_fd, buf, len, 0);
        }
    });

    // Block main thread until QEMU gracefully disconnects, then cleanup
    char sync_buf[1];
    recv(client_fd, sync_buf, 1, 0);
    
    close(client_fd);
    close(server_fd);
    unlink(socket_path);
    
    return 0;
}

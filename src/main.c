/* main.c
 *   by Alex Chadwick
 * 
 * Copyright (C) 2014, Alex Chadwick
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

#include "main.h"

#ifdef _WIN32
#undef _WIN32
#endif

#include <fat.h>
#include <malloc.h>
#include <ogc/consol.h>
#include <ogc/lwp.h>
#include <ogc/system.h>
#include <ogc/video.h>
#include <gccore.h>
#include <sdcard/wiisd_io.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wiiuse/wpad.h>

#include "apploader/apploader.h"
#include "library/dolphin_os.h"
#include "library/event.h"
#include "modules/module.h"
#include "network.h"
#include "search/search.h"
#include "settings/settings.h"
#include "threads.h"

event_t main_event_fat_loaded;

static void Main_PrintSize(size_t size);
static void ConnectHOST(void);
static void ConnectGUEST(void);
static void Relay_LoadSecret(void);
static int Relay_Connect(int is_host);
static int Relay_DebugConnect(int is_host);
static void Relay_DebugLog(const char *message);
static void Relay_DebugLogState(const char *event, int is_host, int game_socket);
static int reliable_send(int socket, const void *data, int size);
static int reliable_recv(int socket, void *data, int size);

static short current_running_ios = 0;

int host_ip = 0xc0a80121;
int port = 10000;
int relay_enabled = 1;
int relay_ip = 0x9bf8df22;
int relay_port = 10000;

#define RELAY_SECRET_MAX 128
#define RELAY_HELLO_SIZE 8
#define RELAY_ROLE_HOST 1
#define RELAY_ROLE_GUEST 2

static char relay_secret[RELAY_SECRET_MAX];
static int relay_secret_len = 0;
static int relay_debug_socket = -1;
static const char relay_secret_path[] = APP_PATH "/relay_secret.txt";

int main(void) {
    int ret;
    void *frame_buffer = NULL;
    GXRModeObj *rmode = NULL;   
	
    /* The game's boot loader is statically loaded at 0x81200000, so we'd better
     * not start mallocing there! */
    SYS_SetArena1Hi((void *)0x81200000);
	settings_init();
	
    /* initialise all subsystems */
    if (!Event_Init(&main_event_fat_loaded))
        goto exit_error;
    if (!IOSApploader_Init())
        goto exit_error;
    if (!Apploader_Init())
        goto exit_error;
    if (!Module_Init())
        goto exit_error;
    if (!Search_Init())
        goto exit_error;

    current_running_ios = *(short *)0x80003140;
    
    /* main thread is UI, so set thread prior to UI */
    LWP_SetThreadPriority(LWP_GetSelf(), THREAD_PRIO_UI);

    /* configure the video */
    VIDEO_Init();
    
    rmode = VIDEO_GetPreferredMode(NULL);
    
    frame_buffer = MEM_K0_TO_K1(SYS_AllocateFramebuffer(rmode));
    if (!frame_buffer)
        goto exit_error;
    console_init(
        frame_buffer, 20, 20, rmode->fbWidth, rmode->xfbHeight,
        rmode->fbWidth * VI_DISPLAY_PIX_SZ);

    VIDEO_Configure(rmode);
    VIDEO_SetNextFramebuffer(frame_buffer);
    VIDEO_SetBlack(false);
    VIDEO_Flush();
    VIDEO_WaitVSync();
    if (rmode->viTVMode & VI_NON_INTERLACE)
        VIDEO_WaitVSync();

    /* display the welcome message */
    printf("\x1b[2;0H");
	printf("Wii NetPlay Test Edition by MrBean35000vr and Chadderz\n");
    printf("Based on BrainSlug Wii  v%x.%02x.%04x"
#ifndef NDEBUG
        " DEBUG build"
#endif
        "\n",
        BSLUG_VERSION_MAJOR(BSLUG_LOADER_VERSION),
        BSLUG_VERSION_MINOR(BSLUG_LOADER_VERSION),
        BSLUG_VERSION_REVISION(BSLUG_LOADER_VERSION));
    printf(" by Chadderz\n\n");

	printf("This software will attempt to patch the inserted disc for NetPlay.\n"
		"This is an extremely early build! It probably doesn't work with many games!\n"
		"It is currently designed primarily for New Super Mario Bros. Wii, and works\nreasonably well.\n"
		"It would be best to be in verbal contact with the other players to work out if\na desync has occurred.\n"
		"Save files are NOT synced, so make sure everyone has the same one!\n"
		"All settings, including host IP and port, should be set manually in the\n" APP_PATH "/config.ini file.\n"
		"Currently, only two players are supported.\n\n");

    if (!IOSApploader_RunBackground())
        goto exit_error;

    printf("Waiting for game disk...\n");
    Event_Wait(&apploader_event_got_disc_id);
    Event_Wait(&apploader_event_got_ios);
	printf("Game ID: %.4s Version %d\nMake sure all players use the same version!\n", os0->disc.gamename, (int)os0->disc.gamever + 1);
    printf("Game wants IOS%d. Reloading... ", _apploader_game_ios);

    int ios_reload_result = IOS_ReloadIOS(_apploader_game_ios);
    if (ios_reload_result < 0)
    {
        printf(
            "\nIOS%d reload failed (%d). Continuing under IOS%d.\n",
            _apploader_game_ios,
            ios_reload_result,
            current_running_ios);
        if (!Apploader_RunBackground(1))
            goto exit_error;
    }
    else
    {
        printf("waiting... ");
        while (*(short *)0x80003140 != _apploader_game_ios)
        {
        }
        printf("done.\n");
        if (!Apploader_RunBackground(0))
            goto exit_error;
    }

    if (!Module_RunBackground())
        goto exit_error;
    if (!Search_RunBackground())
        goto exit_error;

	WPAD_Init();

    if (!__io_wiisd.startup() || !__io_wiisd.isInserted()) {
        printf("Please insert an SD card.\n\n");
        do {
            __io_wiisd.shutdown();
        } while (!__io_wiisd.startup() || !__io_wiisd.isInserted());
    }
    __io_wiisd.shutdown();
    
    if (!fatMountSimple("sd", &__io_wiisd)) {
        fprintf(stderr, "Could not mount SD card.\n");
        goto exit_error;
    }
    
    Event_Trigger(&main_event_fat_loaded);
	
	settings_load();
	Relay_LoadSecret();
        
    printf("Loading modules...\n");
    Event_Wait(&module_event_list_loaded);
    if (module_list_count == 0) {
        printf("No valid modules found!\nCheck the files are in the right place!\n");
		printf("\nPress RESET to exit.\n");
        goto exit_error;
    } else {
        size_t module;
        
        printf(
            "%u module%s found.\n",
            module_list_count, module_list_count > 1 ? "s" : "");
        
        for (module = 0; module < module_list_count; module++) {
            printf(
                "\t%s %s by %s (", module_list[module]->name,
                module_list[module]->version, module_list[module]->author);
            Main_PrintSize(module_list[module]->size);
            puts(").");
        }
        
        Main_PrintSize(module_list_size);
        puts(" total.");
    }
    
	printf("\nPlease wait while the game is patched.\nIf nothing happens after about 2 minutes, reset the machine!\n");

    Event_Wait(&apploader_event_complete);
    Event_Wait(&module_event_complete);
    fatUnmount("sd");
    __io_wiisd.shutdown();
    
    if (module_has_error) {
        printf("\nPress RESET to exit.\n");
        goto exit_error;
    }
    
    if (apploader_game_entry_fn == NULL) {
        fprintf(stderr, "Error... entry point is NULL.\n");
    } else {
        if (module_has_info || search_has_info) {
            printf("\nPress RESET to launch game.\n");
            
            while (!SYS_ResetButtonDown())
                VIDEO_WaitVSync();
            while (SYS_ResetButtonDown())
                VIDEO_WaitVSync();
        }
		
		printf("\nReady! Please select on your Wii Remote:\nA button: Host, B button: Guest\n(Make sure the host goes first).\n");
		while(1)
		{
			WPAD_ScanPads();
			if(WPAD_ButtonsDown(0) & WPAD_BUTTON_A)
			{
				printf("HOST. Please wait, initialising network...\n");
				ConnectHOST();
				break;
			}
			else if(WPAD_ButtonsDown(0) & WPAD_BUTTON_B)
			{
				printf("GUEST. Please wait, initialising network...\n");
				ConnectGUEST();
				break;
			}
		}

		Relay_DebugLog("loader about to call game entry");
        SYS_ResetSystem(SYS_SHUTDOWN, 0, 0);
        apploader_game_entry_fn();
    }

    ret = 0;
    goto exit;
exit_error:
    ret = -1;
exit:
    while (!SYS_ResetButtonDown())
        VIDEO_WaitVSync();
    while (SYS_ResetButtonDown())
        VIDEO_WaitVSync();
    
    VIDEO_SetBlack(true);
    VIDEO_Flush();
    VIDEO_WaitVSync();
    
    free(frame_buffer);
        
    exit(ret);
        
    return ret;
}

static void Main_PrintSize(size_t size) {
    static const char *suffix[] = { "bytes", "KiB", "MiB", "GiB" };
    unsigned int magnitude, precision;
    float sizef;

    sizef = size;
    magnitude = 0;
    while (sizef > 512) {
        sizef /= 1024.0f;
        magnitude++;
    }
    
    assert(magnitude < 4);
    
    if (magnitude == 0)
        precision = 0;
    else if (sizef >= 100)
        precision = 0;
    else if (sizef >= 10)
        precision = 1;
    else
        precision = 2;
        
    printf("%.*f %s", precision, sizef, suffix[magnitude]);
}

static void Relay_LoadSecret(void)
{
	FILE *file = fopen(relay_secret_path, "rb");
	int ch;

	relay_secret_len = 0;
	if (!file)
	{
		return;
	}

	while (relay_secret_len < RELAY_SECRET_MAX && (ch = fgetc(file)) != EOF)
	{
		if (ch == '\r' || ch == '\n')
		{
			break;
		}
		relay_secret[relay_secret_len++] = (char)ch;
	}
	fclose(file);
}

static int reliable_send(int socket, const void *data, int size)
{
	int i;
	for (i = 0; i < size; )
	{
		int amt = Mynet_send(socket, (const char *)data + i, size - i, 0);
		if (amt <= 0) return amt;
		i += amt;
	}
	return size;
}

static int reliable_recv(int socket, void *data, int size)
{
	int i;
	for (i = 0; i < size; )
	{
		int amt = Mynet_recv(socket, (char *)data + i, size - i, 0);
		if (amt <= 0) return amt;
		i += amt;
	}
	return size;
}

static int Relay_Connect(int is_host)
{
	if (relay_secret_len <= 0)
	{
		printf("Relay is enabled, but %s is missing or empty.\n", relay_secret_path);
		return -1;
	}

	if (Mynet_init())
	{
		return -1;
	}

	int sock = Mynet_socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
	if (sock == -1)
	{
		return -1;
	}

	struct sockaddr_in relayAddress = {};
	relayAddress.sin_family = AF_INET;
	relayAddress.sin_len = 8;
	relayAddress.sin_port = relay_port;
	relayAddress.sin_addr.s_addr = relay_ip;

	printf("Connecting to relay %d.%d.%d.%d:%d...\n",
		(int)((relay_ip >> 24) & 0xff), (int)((relay_ip >> 16) & 0xff),
		(int)((relay_ip >> 8) & 0xff), (int)(relay_ip & 0xff), relay_port);

	if (Mynet_connect(sock, (struct sockaddr*)&relayAddress, relayAddress.sin_len))
	{
		Mynet_close(sock);
		return -1;
	}

	int on = 0;
	Mynet_setsockopt(sock, 0, TCP_NODELAY, (char *) &on, sizeof(on));

	unsigned char hello[RELAY_HELLO_SIZE] = {
		'N', 'S', 'R', '1',
		(unsigned char)(is_host ? RELAY_ROLE_HOST : RELAY_ROLE_GUEST),
		0,
		(unsigned char)((relay_secret_len >> 8) & 0xff),
		(unsigned char)(relay_secret_len & 0xff),
	};

	if (reliable_send(sock, hello, sizeof(hello)) != sizeof(hello) ||
		reliable_send(sock, relay_secret, relay_secret_len) != relay_secret_len)
	{
		Mynet_close(sock);
		return -1;
	}

	char response[4];
	if (reliable_recv(sock, response, sizeof(response)) != sizeof(response) ||
		memcmp(response, "OKAY", sizeof(response)) != 0)
	{
		Mynet_close(sock);
		return -1;
	}

	printf("Relay paired. Starting game handshake...\n");
	return sock;
}

static int Relay_DebugConnect(int is_host)
{
	if (relay_secret_len <= 0)
	{
		return -1;
	}

	int sock = Mynet_socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
	if (sock == -1)
	{
		return -1;
	}

	struct sockaddr_in relayAddress = {};
	relayAddress.sin_family = AF_INET;
	relayAddress.sin_len = 8;
	relayAddress.sin_port = relay_port;
	relayAddress.sin_addr.s_addr = relay_ip;

	if (Mynet_connect(sock, (struct sockaddr*)&relayAddress, relayAddress.sin_len))
	{
		Mynet_close(sock);
		return -1;
	}

	int on = 0;
	Mynet_setsockopt(sock, 0, TCP_NODELAY, (char *) &on, sizeof(on));

	unsigned char hello[RELAY_HELLO_SIZE] = {
		'N', 'S', 'D', '1',
		(unsigned char)(is_host ? RELAY_ROLE_HOST : RELAY_ROLE_GUEST),
		0,
		(unsigned char)((relay_secret_len >> 8) & 0xff),
		(unsigned char)(relay_secret_len & 0xff),
	};

	if (reliable_send(sock, hello, sizeof(hello)) != sizeof(hello) ||
		reliable_send(sock, relay_secret, relay_secret_len) != relay_secret_len)
	{
		Mynet_close(sock);
		return -1;
	}

	return sock;
}

static void Relay_DebugLog(const char *message)
{
	char buffer[192];
	int len;

	if (relay_debug_socket < 0)
	{
		return;
	}

	len = snprintf(buffer, sizeof(buffer), "%s\n", message);
	if (len < 0)
	{
		return;
	}
	if (len >= (int)sizeof(buffer))
	{
		len = sizeof(buffer) - 1;
		buffer[len - 1] = '\n';
	}

	Mynet_send(relay_debug_socket, buffer, len, 0);
}

static void Relay_DebugLogState(const char *event, int is_host, int game_socket)
{
	char buffer[160];
	sprintf(
		buffer,
		"loader %s role=%s game_sock=%d debug_sock=%d net_fd=%d",
		event,
		is_host ? "host" : "guest",
		game_socket,
		relay_debug_socket,
		net_ip_top_fd);
	Relay_DebugLog(buffer);
}

static void ConnectHOST(void)
{
	int communicationSock;

	if (relay_enabled)
	{
		communicationSock = Relay_Connect(1);
		if (communicationSock == -1)
		{
			goto error;
		}
		relay_debug_socket = Relay_DebugConnect(1);
		Relay_DebugLogState("relay paired", 1, communicationSock);
	}
	else
	{
		if(Mynet_init())
		{
			goto error;
		}
		int sock = Mynet_socket(AF_INET, SOCK_STREAM, IPPROTO_IP);

		if (sock == -1)
		{
			goto error;
		}

		struct sockaddr_in myAddress = {};
		myAddress.sin_family = AF_INET;
		myAddress.sin_len = 8;
		myAddress.sin_port = port;
		myAddress.sin_addr.s_addr = Mynet_gethostip();

		if(Mynet_bind(sock, (struct sockaddr*)&myAddress, myAddress.sin_len))
		{
			goto error;
		}

		if(Mynet_listen(sock, 1))
		{
			goto error;
		}

		printf("Listening on %d.%d.%d.%d:%d...\n", (int)((myAddress.sin_addr.s_addr >> 24) & 0xff), (int)((myAddress.sin_addr.s_addr >> 16) & 0xff), (int)((myAddress.sin_addr.s_addr >> 8) & 0xff), (int)(myAddress.sin_addr.s_addr & 0xff), port);

		struct sockaddr_in theirAddress = {};
		int theirAddressLength = 8;

		communicationSock = Mynet_accept(sock, (struct sockaddr*)&theirAddress, (u32*)&theirAddressLength);

		if (communicationSock == -1)
		{
			goto error;
		}

		int on = 0;
		Mynet_setsockopt(communicationSock, 0, TCP_NODELAY, (char *) &on, sizeof(on));
	}

	printf("Connected! Sending start request!");

	int letsgo = 1;

	if(Mynet_send(communicationSock, &letsgo, sizeof(letsgo), 0) != sizeof(letsgo))
	{
		goto error;
	}

	printf(" OK!\nWaiting for response...");

	int whatigot = 0;

	if(Mynet_recv(communicationSock, &whatigot, sizeof(whatigot), 0) != sizeof(whatigot))
	{
		goto error;
	}

	printf(" OK!\n");

	if (whatigot == 1)
	{
		printf("That was good.\n");
		int* sockPointer = (int*)Search_SymbolLookup("communicationSock");
		*sockPointer = communicationSock;
		int* net_ip_top_fd_pointer = (int*)Search_SymbolLookup("net_ip_top_fd");
		*net_ip_top_fd_pointer = net_ip_top_fd;
		int* host_pointer = (int*)Search_SymbolLookup("host");
		*host_pointer = 1;
		int* relay_debug_sock_pointer = (int*)Search_SymbolLookup("relayDebugSock");
		*relay_debug_sock_pointer = relay_debug_socket;
		Relay_DebugLogState("exported module symbols", 1, communicationSock);
		return;
	}
	else
	{
		goto error;
	}

error:
	printf("\nWell, that didn't work! Press RESET to get us out of here and try again.\nIf you don't have a RESET button, I feel sorry for you.\n");
	while (!SYS_ResetButtonDown())
        VIDEO_WaitVSync();
    while (SYS_ResetButtonDown())
        VIDEO_WaitVSync();
	exit(0);
	
	
}

static void ConnectGUEST(void)
{
	int sock;

	if (relay_enabled)
	{
		sock = Relay_Connect(0);
		if (sock == -1)
		{
			goto error;
		}
		relay_debug_socket = Relay_DebugConnect(0);
		Relay_DebugLogState("relay paired", 0, sock);
	}
	else
	{
		if(Mynet_init())
		{
			goto error;
		}
		sock = Mynet_socket(AF_INET, SOCK_STREAM, IPPROTO_IP);

		if (sock == -1)
		{
			goto error;
		}

		struct sockaddr_in hostAddress = {};
		hostAddress.sin_family = AF_INET;
		hostAddress.sin_len = 8;
		hostAddress.sin_port = port;
		hostAddress.sin_addr.s_addr = host_ip;

		printf("Connecting to %d.%d.%d.%d:%d...\n", (int)((host_ip >> 24) & 0xff), (int)((host_ip >> 16) & 0xff), (int)((host_ip >> 8) & 0xff), (int)(host_ip & 0xff), port);

		if(Mynet_connect(sock, (struct sockaddr*)&hostAddress, hostAddress.sin_len))
		{
			goto error;
		}
		
		int on = 0;
		Mynet_setsockopt(sock, 0, TCP_NODELAY, (char *) &on, sizeof(on));
	}



	printf("Connected! Waiting for start request!");

	int whatigot = 0;

	if(Mynet_recv(sock, &whatigot, sizeof(whatigot), 0) != sizeof(whatigot))
	{
		goto error;
	}



	printf(" OK!\nSending a response...");

	int letsgo = 1;

	if(Mynet_send(sock, &letsgo, sizeof(letsgo), 0) != sizeof(letsgo))
	{
		goto error;
	}

	printf(" OK!\n");

	if (whatigot == 1)
	{
		printf("That was good.\n");
		int* sockPointer = (int*)Search_SymbolLookup("communicationSock");
		*sockPointer = sock;
		int* net_ip_top_fd_pointer = (int*)Search_SymbolLookup("net_ip_top_fd");
		*net_ip_top_fd_pointer = net_ip_top_fd;
		int* host_pointer = (int*)Search_SymbolLookup("host");
		*host_pointer = 0;
		int* relay_debug_sock_pointer = (int*)Search_SymbolLookup("relayDebugSock");
		*relay_debug_sock_pointer = relay_debug_socket;
		Relay_DebugLogState("exported module symbols", 0, sock);
		return;
	}
	else
	{
		goto error;
	}

error:
	printf("\nWell, that didn't work! Press RESET to get us out of here and try again.\nIf you don't have a RESET button, I feel sorry for you.\n");
	while (!SYS_ResetButtonDown())
        VIDEO_WaitVSync();
    while (SYS_ResetButtonDown())
        VIDEO_WaitVSync();
	exit(0);
}

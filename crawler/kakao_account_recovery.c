// Local, bounded account-ID hash matching for explicit first-run setup only.
#include <CommonCrypto/CommonDigest.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

static unsigned char target[CC_SHA512_DIGEST_LENGTH];
static atomic_long found = -1;
static atomic_bool stopped = false;
static long maximum;
static int lanes;

static void *worker(void *arg) {
    long lane = (long)arg;
    char text[24];
    unsigned char hash[CC_SHA512_DIGEST_LENGTH];
    for (long n = lane + 1; n <= maximum && !atomic_load(&stopped); n += lanes) {
        int length = snprintf(text, sizeof(text), "%ld", n);
        CC_SHA512(text, (CC_LONG)length, hash);
        if (memcmp(hash, target, sizeof(target)) == 0) {
            atomic_store(&found, n);
            atomic_store(&stopped, true);
        }
    }
    return NULL;
}

int main(void) {
    char hex[129];
    if (scanf("%128s %ld %d", hex, &maximum, &lanes) != 3 || strlen(hex) != 128 ||
        maximum < 1 || maximum > 1000000000L || lanes < 1 || lanes > 8) return 2;
    for (int i = 0; i < 64; i++) {
        unsigned int byte;
        if (!strchr("0123456789abcdef", hex[2*i]) ||
            !strchr("0123456789abcdef", hex[2*i+1]) ||
            sscanf(hex + 2*i, "%2x", &byte) != 1) return 2;
        target[i] = (unsigned char)byte;
    }
    pthread_t threads[8];
    int created = 0;
    for (long i = 0; i < lanes; i++) {
        if (pthread_create(&threads[i], NULL, worker, (void *)i) != 0) {
            atomic_store(&stopped, true);
            break;
        }
        created++;
    }
    for (int i = 0; i < created; i++) pthread_join(threads[i], NULL);
    if (created != lanes) return 3;
    if (atomic_load(&found) < 0) return 1;
    printf("%ld", atomic_load(&found));
    return 0;
}

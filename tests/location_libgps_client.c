/* Development-only native GPSD consumer. Connects only to an ephemeral fixture. */
#include <gps.h>
#include <math.h>
#include <stdio.h>

int main(int argc, char **argv) {
    struct gps_data_t data;
    if (argc != 2 || gps_open("127.0.0.1", argv[1], &data) != 0) return 1;
    if (gps_stream(&data, WATCH_ENABLE | WATCH_JSON, NULL) != 0) return 2;
    for (int i = 0; i < 16; i++) {
        if (!gps_waiting(&data, 500000)) continue;
        if (gps_read(&data, NULL, 0) < 0) return 3;
        if (!(data.set & LATLON_SET) || data.fix.mode < MODE_2D) continue;
        if (fabs(data.fix.latitude - 12.5) > 0.00001 ||
            fabs(data.fix.longitude + 34.5) > 0.00001) return 4;
        if (!isnan(data.fix.speed) || data.fix.time.tv_sec != 0) return 5;
        puts("manual-position-without-gps-time");
        gps_stream(&data, WATCH_DISABLE, NULL);
        gps_close(&data);
        return 0;
    }
    gps_close(&data);
    return 6;
}

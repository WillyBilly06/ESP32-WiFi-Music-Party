idf_component_register(SRCS "main.c" "settings.c" "radio.c" "bluetooth.c" "gatt.c" "audio.c" "source_clock.c" "telemetry.c" "coex_trace.c"
    INCLUDE_DIRS "." REQUIRES wmp_protocol wmp_codecs esp_wifi esp_coex esp_event esp_netif esp_timer nvs_flash bt btstack esp_driver_uart esp_driver_i2s esp_hal_clock esp_hw_support efuse heap mbedtls)
target_compile_definitions(${COMPONENT_LIB} PRIVATE WMP_BUILD_ID="${WMP_BUILD_ID}")
# Observe the coexistence scheduler without modifying the SDK: interpose the
# BT controller's scheme-callback registration (see coex_trace.c).
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=coex_schm_register_callback" "-Wl,--wrap=coex_wifi_request" "-Wl,--wrap=coex_wifi_release" "-Wl,--wrap=coex_bt_request" "-Wl,--wrap=coex_bt_release")

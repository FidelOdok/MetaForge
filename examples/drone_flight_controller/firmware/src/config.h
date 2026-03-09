/**
 * MetaForge DroneFC - Configuration Header
 *
 * Board-level configuration defines for the Quadcopter
 * Flight Controller. Generated from constraints.json and
 * pinmap.json by MetaForge.
 */

#ifndef DRONEFC_CONFIG_H
#define DRONEFC_CONFIG_H

/* ================================================================
 * System Clock Configuration
 * ================================================================ */
#define SYSCLK_MHZ              168
#define HSE_VALUE               8000000U    /* 8 MHz external crystal */
#define PLL_M                   8
#define PLL_N                   336
#define PLL_P                   2
#define PLL_Q                   7           /* USB OTG FS = 48 MHz */
#define AHB_PRESCALER           1           /* HCLK = 168 MHz */
#define APB1_PRESCALER          4           /* APB1 = 42 MHz */
#define APB2_PRESCALER          2           /* APB2 = 84 MHz */
#define FLASH_LATENCY           5           /* 5 wait states at 168 MHz */

/* ================================================================
 * Peripheral Enable Flags
 * ================================================================ */
#define ENABLE_SPI1             1   /* IMU (MPU6050) */
#define ENABLE_I2C1             1   /* Barometer (BMP280) */
#define ENABLE_USART1           1   /* GPS (NEO-M8N) */
#define ENABLE_USART2           1   /* Telemetry / OSD */
#define ENABLE_USART3           1   /* RC receiver (SBUS/CRSF) */
#define ENABLE_USART6           1   /* Spare */
#define ENABLE_TIM1             1   /* Motor DShot outputs */
#define ENABLE_ADC1             1   /* Battery voltage */
#define ENABLE_USB_OTG_FS       1   /* USB-C */
#define ENABLE_SDIO             1   /* Micro-SD blackbox */
#define ENABLE_DMA              1   /* DMA for SPI, UART, DShot */

/* ================================================================
 * Flight Controller Parameters
 * ================================================================ */
#define PID_LOOP_RATE_HZ        8000
#define PID_LOOP_PERIOD_US      125     /* 1000000 / 8000 */

/* Default PID gains (can be overridden via configurator) */
#define PID_ROLL_P              4.0f
#define PID_ROLL_I              3.0f
#define PID_ROLL_D              2.5f
#define PID_PITCH_P             4.0f
#define PID_PITCH_I             3.0f
#define PID_PITCH_D             2.5f
#define PID_YAW_P               5.0f
#define PID_YAW_I               4.5f
#define PID_YAW_D               0.0f

/* Maximum interrupt latency constraint (microseconds) */
#define MAX_IRQ_LATENCY_US      10

/* ================================================================
 * Motor Configuration
 * ================================================================ */
#define MOTOR_COUNT             4
#define MOTOR_PROTOCOL_DSHOT600 1
#define DSHOT_BIT_PERIOD_NS     1670    /* 1.67 us per bit */
#define MOTOR_IDLE_THROTTLE     0       /* DShot command 0 = disarmed */

/* Motor mixing table (quadcopter X configuration)
 *   Motor 1: Front-Right (CW)  -> TIM1_CH1
 *   Motor 2: Rear-Right  (CCW) -> TIM1_CH2
 *   Motor 3: Rear-Left   (CW)  -> TIM1_CH3
 *   Motor 4: Front-Left  (CCW) -> TIM1_CH4
 */

/* ================================================================
 * IMU Configuration (MPU6050)
 * ================================================================ */
#define IMU_SPI                 SPI1
#define IMU_SPI_CLOCK_HZ        21000000    /* 21 MHz (APB2/4) */
#define IMU_GYRO_RANGE          2000        /* +/- 2000 deg/s */
#define IMU_ACCEL_RANGE         16          /* +/- 16g */
#define IMU_SAMPLE_RATE_HZ      8000
#define IMU_DLPF_BW_HZ         256

/* ================================================================
 * Barometer Configuration (BMP280)
 * ================================================================ */
#define BARO_I2C                I2C1
#define BARO_I2C_ADDR           0x76        /* SDO pin low */
#define BARO_SAMPLE_RATE_HZ     200         /* Read every 40th PID cycle */
#define BARO_OVERSAMPLING_P     16
#define BARO_OVERSAMPLING_T     2

/* ================================================================
 * GPS Configuration (NEO-M8N)
 * ================================================================ */
#define GPS_UART                USART1
#define GPS_BAUD                115200
#define GPS_UPDATE_RATE_HZ      10

/* ================================================================
 * Battery Monitoring
 * ================================================================ */
#define VBAT_ADC                ADC1
#define VBAT_ADC_CHANNEL        10          /* PC0 = ADC1_IN10 */
#define VBAT_DIVIDER_RATIO      10.0f       /* 10:1 voltage divider */
#define VBAT_ADC_RESOLUTION     4096        /* 12-bit ADC */
#define VBAT_REF_VOLTAGE        3.3f        /* ADC reference voltage */
#define VBAT_MIN_CELL_V         3.3f        /* Low voltage warning */
#define VBAT_MAX_CELLS          6           /* 6S LiPo max */

/* ================================================================
 * Blackbox Logging
 * ================================================================ */
#define BLACKBOX_RATE_HZ        1000        /* Log every 8th PID cycle */
#define BLACKBOX_SDIO_CLK_HZ    24000000    /* 24 MHz SDIO clock */

/* ================================================================
 * USB Configuration
 * ================================================================ */
#define USB_VID                 0x0483      /* ST default VID */
#define USB_PID                 0x5740      /* ST CDC VCP PID */
#define USB_MANUFACTURER        "MetaForge"
#define USB_PRODUCT             "DroneFC v1.0"

/* ================================================================
 * Board Constraints (from constraints.json)
 * ================================================================ */
#define BOARD_WIDTH_MM          36
#define BOARD_HEIGHT_MM         36
#define BOARD_MAX_THICKNESS_MM  8
#define BOARD_MAX_WEIGHT_G      15
#define INPUT_VOLTAGE_MIN_V     10.8f       /* 3S LiPo minimum */
#define INPUT_VOLTAGE_MAX_V     25.2f       /* 6S LiPo maximum */
#define POWER_BUDGET_W          3.5f
#define OPERATING_TEMP_MIN_C    (-10)
#define OPERATING_TEMP_MAX_C    60

#endif /* DRONEFC_CONFIG_H */

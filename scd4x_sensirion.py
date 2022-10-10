"""SCD4x Sensirion module"""

from sensor_pack import bus_service
from sensor_pack.base_sensor import BaseSensor, Iterator
from sensor_pack import base_sensor
from sensor_pack.crc_mod import crc8
import utime
from sensor_pack import bitfield
import struct
import array


class SCD4xSensirion(BaseSensor, Iterator):
    """Class for work with Sensirion SCD4x sensor"""
    def __init__(self, adapter: bus_service.BusAdapter, address=0x62, check_crc: bool = True):
        """Если check_crc в Истина, то каждый, принятый от датчика пакет данных, проверяется на правильность путем
        расчета контрольной суммы.
        If check_crs is True, then each data packet received from the sensor is checked for correctness by
        calculating the checksum."""
        super().__init__(adapter, address, True)    # Big Endian
        self.check_crc = check_crc
        # power mode
        self._low_power_mode = False
        # measurement mode (single shot, continuous)
        self._single_shot_mode = False
        # for send cmd
        # self.buf = bytearray(5)

    def _read(self, n_bytes: int) -> bytes:
        return self.adapter.read(self.address, n_bytes)

    def _write(self, buf: bytes) -> bytes:
        return self.adapter.write(self.address, buf)

    @staticmethod
    def _calc_crc(sequence) -> int:
        return crc8(sequence, 0x31, 0xFF)

    def _send_command(self, cmd: int, value: [bytes, None],
                      wait_time: int = 0, bytes_for_read: int = 0) -> [bytes, None]:
        """Передает команду датчику по шине.
        cmd - код команды.
        value - последовательность, передаваемая после кода команды.
        wait_time - время в мс. которое нужно подождать для обработки команды датчиком.
        bytes_for_read - количество байт в ответе датчика, если не 0, то будет считан ответ,
        проверена CRC (зависит от self.check_crc) и этот ответ будет возвращен, как результат"""
        raw_cmd = cmd.to_bytes(2, "big")
        raw_out = raw_cmd
        if value:
            raw_out += value    # добавляю value и crc
            raw_out += SCD4xSensirion._calc_crc(value).to_bytes(1, "big")     # crc считается только для данных!
        self._write(raw_out)    # выдача на шину
        if wait_time:
            utime.sleep_ms(wait_time)   # ожидание
        if not bytes_for_read:
            return None
        b = self._read(bytes_for_read)  # читаю с шины с проверкой количества считанных байт
        base_sensor.check_value(len(b), (bytes_for_read,),
                                f"Invalid buffer length for cmd: {cmd}. Received {len(b)} out of {bytes_for_read}")
        return b    # возврат считанного bytearray

    # BaseSensor
    # Advanced features
    def save_config(self):
        """Настройки конфигурации, такие как смещение температуры, высота над уровнем моря датчика
        по умолчанию сохраняются только в энергозависимой памяти (ОЗУ) и будут потеряны после выключения и включения
        питания. Метод сохраняет текущую конфигурацию в EEPROM SCD4x, сохраняя ее при отключении питания.
        Чтобы избежать ненужного износа EEPROM, метод следует вызывать только в том случае, если это необходимо(!) и
        если были внесены фактические изменения в конфигурацию. EEPROM гарантированно выдерживает не менее 2000
        циклов записи до отказа(!).
        Configuration settings such as temperature offset, sensor altitude are stored by default only in volatile memory
        (RAM) and will be lost after a power cycle. The method saves the current configuration in the EEPROM of the
        SCD4x, saving it when the power is turned off. To avoid unnecessary wear on the EEPROM, the method should only
        be called if necessary(!) and if actual configuration changes have been made.
        EEPROM is guaranteed to withstand at least 2000 write cycles to failure (!)"""
        cmd = 0x3615
        self._send_command(cmd, None, 800)

    def get_id(self) -> tuple:
        """Return 3 words of unique serial number can be used to identify
        the chip and to verify the presence of the sensor."""
        # создатели датчика 'обрадовали'. вместо подсчета одного байта CRC на 6 байт (3 двухбайтных слова)
        # они считают CRC для каждого из 3-х двухбайтных слов!
        cmd = 0x3682
        b = self._send_command(cmd, None, 0, bytes_for_read=9)
        if self.check_crc:
            crc_from_buf = [b[i] for i in range(2, 9, 3)]  # build list of CRC from buf
            # build list of calculated CRC
            calculated_crc = [SCD4xSensirion._calc_crc((b[i], b[i+1])) for i in range(0, 9, 3)]
            # print(crc_from_buf, calculated_crc)
            if crc_from_buf != calculated_crc:      # compare CRC from buf and calculated CRC
                base_sensor.check_value(1, (0,),    # Fail!
                                        f"Invalid СRC value(s): received: {crc_from_buf}, calculated: {calculated_crc}")
        # return result
        return tuple([(b[i] << 8) | b[i+1] for i in range(0, 9, 3)])    # Success

    def soft_reset(self):
        """Я сознательно не стал использовать коменду perfom_factory_reset, чтобы было невозможно испортить датчик
        программным путем, так-как количество циклов записи во внутреннюю FLASH память датчика ограничено!
        I deliberately did not use the perfom_factory_reset command, so that it would be impossible to spoil the
        sensor programmatically, since the number of write cycles to the internal FLASH memory of the
        sensor is limited!"""
        return None

    def exec_self_test(self) -> bool:
        """"Этот метод можно использовать в качестве конечного теста для проверки работоспособности датчика и
        проверки подачи питания на датчик. Возвращает Истина, когда тест пройден успешно.
        The feature can be used as an end-of-line test to check sensor functionality and the customer power
        supply to the sensor. Returns True when the test is successful."""
        cmd = 0x3639
        self._write(cmd.to_bytes(2, "big"))
        utime.sleep(10)  # да, ждать 10 секунд! yes, wait 10 seconds!
        b = self._read(3)
        base_sensor.check_value(len(b), (3,), f"Invalid buffer length (exec_self_test): {len(b)}")
        res = self.unpack("H", b)[0]
        if self.check_crc:
            crc = SCD4xSensirion._calc_crc(res.to_bytes(2, "big"))
            base_sensor.check_value(1, (0,),  # Fail!
                                    f"Invalid СRC value(s): received: {b[2]}, calculated: {crc}")
        return 0 == res

    def reinit(self) -> None:
        """Команда reinit повторно инициализирует датчик, загружая пользовательские настройки из EEPROM.
        Перед отправкой команды reinit необходимо выполнить метод stop_measurement. Если команда reinit не вызывает
        желаемой повторной инициализации, к SCD4x следует применить цикл включения и выключения питания.
        The reinit command reinitializes the sensor by reloading user settings from EEPROM.
        Before sending the reinit command, the stop_measurement method must be called.
        If the reinit command does not trigger the desired re-initialization,
        a power-cycle should be applied to the SCD4x."""
        cmd = 0x3646
        self._write(cmd.to_bytes(2, "big"))
        utime.sleep_ms(20)

    # On-chip output signal compensation
    def set_temperature_offset(self, offset: float):
        """Смещение температуры не влияет на точность измерения CO2 . Правильная установка смещения температуры SCD4x
        внутри пользовательского устройства позволяет пользователю использовать выходные сигналы RH и T. Обратите
        внимание, что смещение температуры может зависеть от различных факторов, таких как режим измерения SCD4x,
        самонагрев близких компонентов, температура окружающей среды и расход воздуха. Таким образом, смещение
        температуры SCD4x должно определяться внутри пользовательского устройства в типичных условиях его работы
        (включая режим работы, который будет использоваться в приложении) и при тепловом равновесии. По умолчанию
        смещение температуры установлено в 4°C.
        The temperature offset has no influence on the SCD4x CO 2 accuracy. Setting the temperature offset of the SCD4x
        inside the customer device correctly allows the user to leverage the RH and T output signal. Note that the
        temperature offset can depend on various factors such as the SCD4x measurement mode, self-heating of close
        components, the ambient temperature and air flow.
        𝑇 𝑜𝑓𝑓𝑠𝑒𝑡_𝑎𝑐𝑡𝑢𝑎𝑙 = 𝑇 𝑆𝐶𝐷40 − 𝑇 𝑅𝑒𝑓𝑒𝑟𝑒𝑛𝑐𝑒 + 𝑇 𝑜𝑓𝑓𝑠𝑒𝑡_ 𝑝𝑟𝑒𝑣𝑖𝑜𝑢𝑠"""
        cmd = 0x241D
        offset_raw = int(374.49142857 * offset)
        crc = SCD4xSensirion._calc_crc(offset_raw.to_bytes(2, "big"))
        self._write(cmd.to_bytes(2, "big")+offset_raw.to_bytes(2, "big") + crc.to_bytes(1, "big"))
        utime.sleep_ms(1)

    def get_temperature_offset(self) -> float:
        cmd = 0x2318
        self._write(cmd.to_bytes(2, "big"))
        utime.sleep_ms(1)
        b = self._read(3)
        base_sensor.check_value(len(b), (3,), f"Invalid buffer length (get_temperature_offset): {len(b)}")
        temp_offs = self.unpack("H", b)[0]
        return 0.0026702880859375 * temp_offs

    def set_altitude(self, masl: int):
        """Чтение и запись высоты датчика должны выполняться, когда SCD4x находится в режиме ожидания.
        Как правило, высота датчика устанавливается один раз после установки устройства. Чтобы сохранить настройку
        в EEPROM, необходимо выполнить метод save_config. По умолчанию высота датчика установлена в
        0 метров над уровнем моря (masl).
        Reading and writing sensor height must be done when the SCD4x is in standby mode. As a rule, the height of the
        sensor is set once after the installation of the device. To save the configuration to EEPROM, you must execute
        the save_config method. By default, the sensor height is set to 0 meters above sea level (masl)."""
        cmd = 0x2427
        masl_raw = masl.to_bytes(2, "big")
        crc = SCD4xSensirion._calc_crc(masl_raw)
        self._write(cmd.to_bytes(2, "big") + masl_raw + crc.to_bytes(1, "big"))
        utime.sleep_ms(1)

    def get_altitude(self) -> int:
        cmd = 0x2322
        self._write(cmd.to_bytes(2, "big"))
        utime.sleep_ms(1)
        b = self._read(3)
        base_sensor.check_value(len(b), (3,), f"Invalid buffer length (get_altitude): {len(b)}")
        return self.unpack("H", b)[0]

    def set_ambient_pressure(self, pressure: float):
        """Метод может быть вызван во время периодических измерений, чтобы включить непрерывную компенсацию давления.
        Обратите внимание, что установка давления окружающей среды с помощью set_ambient_pressure отменяет любую
        компенсацию давления, основанную на ранее установленной высоте датчика. Использование этой команды настоятельно
        рекомендуется для приложений со значительными изменениями давления окружающей среды,
        чтобы обеспечить точность датчика.
        The method can be called during periodic measurements to enable continuous pressure compensation.
        Note that setting the ambient pressure using set_ambient_pressure overrides any pressure compensation based
        on the previously set sensor height. The use of this command is highly recommended for applications with
        significant changes in ambient pressure to ensure sensor accuracy."""
        cmd = 0xE000
        press_raw = int(pressure // 100).to_bytes(2, "big")
        crc = SCD4xSensirion._calc_crc(press_raw)
        self._write(cmd.to_bytes(2, "big") + press_raw + crc.to_bytes(1, "big"))
        utime.sleep_ms(1)

    def periodic_measurement(self, start: bool):
        """Start periodic measurement. In low power mode, signal update interval is approximately 30 seconds.
        In normal power mode, signal update interval is approximately 5 seconds."""
        if start:
            cmd = 0x21AC if self._low_power_mode else 0x21B1
        else:   # stop periodic measurement
            cmd = 0x3F86
        self._write(cmd.to_bytes(2, "big"))

    def is_data_ready(self) -> bool:
        cmd = 0xE4B8
        utime.sleep_ms(1)
        b = self._read(3)
        base_sensor.check_value(len(b), (3,), f"Invalid buffer length (is_data_ready): {len(b)}")
        return self.unpack("H", b)[0]



    # Iterator
    def __iter__(self):
        return self

    def __next__(self):
        pass

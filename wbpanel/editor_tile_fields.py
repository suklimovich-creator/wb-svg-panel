"""Library form metadata for options consumed by the tile renderers."""

COMMON = {
    'title': ('Название', 'text'), 'w': ('Базовая ширина', 'size'),
    'h': ('Базовая высота', 'size'), 'icon': ('Иконка', 'text'),
    'subtitle': ('Подзаголовок', 'text'), 'color': ('Цвет плитки', 'text'),
    'show_status': ('Показывать состояние', 'boolean'),
    'stale_after': ('Время до устаревания, с', 'nonnegative'),
}

POWER = {'command_on': ('Команда включения', 'text'), 'command_off': ('Команда выключения', 'text')}
TEMPERATURE = {'temp_min': ('Минимальная температура', 'number'),
               'temp_max': ('Максимальная температура', 'number')}
SCALE = {'scale_min': ('Начало шкалы', 'number'), 'scale_max': ('Конец шкалы', 'number'),
         'dial': ('Циферблат', 'boolean'), 'step': ('Шаг уставки', 'positive')}
AXES = {'grid_labels': ('Значения на сетке', 'boolean'), 'time_labels': ('Подписи времени', 'boolean')}
COVER = {'inverted': ('Обратное направление', 'boolean'), 'facing': ('Направление окна', 'text'),
         'location': ('Координаты окна', 'json', '{"lat":55.75,"lon":37.62}'),
         'weather': ('Устройство погоды', 'text'), 'look': ('Оформление', 'text'),
         'command_open': ('Команда открытия', 'text'), 'command_close': ('Команда закрытия', 'text'),
         'command_stop': ('Команда остановки', 'text')}
CALENDARS = ('Календари', 'json', '["family","work"]')
LIST = ('Имя списка', 'text')
DEVICE = ('Устройство', 'text')
LOOK = ('Оформление', 'text')
VIEW = ('Режим отображения', 'text')
GRID = ('Шаг сетки', 'positive')

FIELDS = {
    'switch': {**POWER, 'invert': ('Инверсия', 'boolean'), 'look': LOOK, 'badge': ('Метка', 'text'),
               'on_text': ('Подпись при включении', 'text'), 'off_text': ('Подпись при выключении', 'text')},
    'scene': {'command_on': ('Команда запуска', 'text')},
    'scenes': {'items': ('Кнопки сцен', 'json', '[{"channel":"dev/scene","title":"Вечер"}]')},
    'header': {'plain': ('Заголовок без фона', 'boolean'),
               'status': ('Чипы состояния', 'json', '[{"channel":"dev/status"}]')},
    'camera': {'camera': ('Имя камеры', 'text'), 'name': ('Запасное имя камеры', 'text')},
    'link': {'panel': ('Панель назначения', 'text'), 'url': ('Адрес ссылки', 'url')},
    'light': {**POWER, **TEMPERATURE, 'look': LOOK, 'temp_unit': ('Шкала температуры', 'choice', 'percent', 'kelvin'),
              'cold_color': ('Холодный цвет', 'text'), 'warm_color': ('Тёплый цвет', 'text')},
    'dimmer': {**POWER, 'look': LOOK}, 'curtain': COVER, 'roller': COVER,
    'thermostat': {**SCALE, 'look': LOOK, 'target_min': ('Минимальная уставка', 'number'),
                   'target_max': ('Максимальная уставка', 'number'), 'digits': ('Знаков после запятой', 'integer', 0, 10),
                   'command_mode_on': ('Команда включения режима', 'text'),
                   'command_mode_off': ('Команда выключения режима', 'text')},
    'fancoil': {**SCALE, **POWER, **TEMPERATURE, 'look': LOOK, 'view': VIEW, 'spin': ('Вращение вентилятора', 'boolean'),
                'modes': ('Режимы', 'json', '{"0":"Охлаждение"}'), 'fans': ('Скорости', 'json', '{"0":"Авто"}'),
                'fan_states': ('Состояния вентилятора', 'json', '{"4":"Остановлен"}')},
    'ac': {**TEMPERATURE, 'device': DEVICE, 'step': ('Шаг уставки', 'positive'),
           'look': LOOK, 'view': VIEW, 'room': ('Комната', 'text')},
    'tv': {'device': DEVICE, 'look': LOOK, 'apps': ('Приложения', 'json', '["app_rutube"]')},
    'chart': {**AXES, 'series': ('Ряды графика', 'json', '[{"channel":"dev/temperature","unit":"°C"}]'),
              'points': ('Количество точек', 'integer', 2, 10000), 'range': ('Период графика', 'duration'),
              'info': ('Легенда', 'boolean'), 'show_span': ('Показывать период', 'boolean')},
    'forecast': {**AXES, 'grid': GRID}, 'clock': {**AXES, 'grid': GRID},
    'leak': {'sensors': ('Датчики протечки', 'json', '["bath"]'), 'channel': ('Канал датчика', 'channel')},
    'valve': {**POWER, 'valve': ('Имя крана', 'text'), 'module': ('Модуль', 'text'),
              'water': ('Вода', 'choice', 'cold', 'hot'), 'look': LOOK, 'invert': ('Инверсия', 'boolean')},
    'agenda': {'calendars': CALENDARS}, 'list': {'list': LIST, 'font': ('Размер шрифта', 'positive')},
    'today': {'calendars': CALENDARS, 'list': LIST},
}

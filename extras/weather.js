// ============================================
// ВИРТУАЛЬНОЕ УСТРОЙСТВО "ПОГОДА" (Open-Meteo)
// Текущая погода + почасовой прогноз на 24 часа:
// температура, осадки, вероятность осадков, облачность, код погоды
// ============================================

var LAT = 55.7558;        // широта, при необходимости измените
var LON = 37.6173;        // долгота
var HOURS = 24;           // глубина прогноза, часов
var STAT_HOURS = 12;      // окно для min/max и сумм
var RAIN_MM = 0.1;        // порог "есть осадки", мм/ч
var RAIN_PROB = 50;       // порог вероятности для rain_expected, %
var PER_HOUR_CELLS = true; // дублировать температуру в temp_00..temp_23

function pad2(n) {
    return n < 10 ? "0" + n : "" + n;
}

// ---- 1. Описание ячеек ----
var cells = {
    // текущее состояние
    temperature:  { title: "Температура",          type: "value",  value: 0,  readonly: true, units: "°C" },
    humidity:     { title: "Влажность",            type: "value",  value: 0,  readonly: true, units: "%" },
    pressure:     { title: "Давление",             type: "value",  value: 0,  readonly: true, units: "мм рт. ст." },
    windspeed:    { title: "Ветер",                type: "value",  value: 0,  readonly: true, units: "км/ч" },
    description:  { title: "Описание",             type: "text",   value: "", readonly: true },
    last_update:  { title: "Последнее обновление", type: "text",   value: "", readonly: true },
    forecast_ok:  { title: "Прогноз актуален",     type: "switch", value: false, readonly: true },

    // ряды прогноза для графика на панели: CSV, HOURS значений, шаг 1 час
    forecast_start:     { title: "Начало прогноза",         type: "text", value: "", readonly: true },
    temp_series:        { title: "Ряд: температура",        type: "text", value: "", readonly: true },
    precip_series:      { title: "Ряд: осадки",             type: "text", value: "", readonly: true },
    precip_prob_series: { title: "Ряд: вероятность осадков", type: "text", value: "", readonly: true },
    cloud_series:       { title: "Ряд: облачность",         type: "text", value: "", readonly: true },
    code_series:        { title: "Ряд: код погоды",         type: "text", value: "", readonly: true },

    // агрегаты
    temp_max_12h:        { title: "Максимум за 12ч",          type: "value",  value: 0, readonly: true, units: "°C" },
    temp_min_12h:        { title: "Минимум за 12ч",           type: "value",  value: 0, readonly: true, units: "°C" },
    precip_now:          { title: "Осадки сейчас",            type: "value",  value: 0, readonly: true, units: "мм/ч" },
    precip_sum_12h:      { title: "Осадки за 12ч",            type: "value",  value: 0, readonly: true, units: "мм" },
    precip_prob_max_12h: { title: "Макс. вероятность за 12ч", type: "value",  value: 0, readonly: true, units: "%" },
    rain_in:             { title: "Осадки через",             type: "value",  value: -1, readonly: true, units: "ч" },
    rain_expected:       { title: "Ожидаются осадки",         type: "switch", value: false, readonly: true },

    // для градиента день/ночь
    sunrise: { title: "Восход", type: "text", value: "", readonly: true },
    sunset:  { title: "Закат",  type: "text", value: "", readonly: true }
};

if (PER_HOUR_CELLS) {
    for (var h = 0; h < HOURS; h++) {
        cells["temp_" + pad2(h)] = {
            title: (h === 0 ? "Текущий час" : "+" + h + " ч"),
            type: "value",
            value: 0,
            readonly: true,
            units: "°C"
        };
    }
}

defineVirtualDevice("weather", {
    title: "Погода",
    cells: cells
});

// ---- 2. Расшифровка кода погоды (WMO) ----
function getWeatherDescription(code) {
    var codes = {
        0: "Ясно", 1: "Облачно", 2: "Облачно", 3: "Пасмурно",
        45: "Туман", 48: "Туман", 51: "Морось", 53: "Морось",
        55: "Морось", 56: "Ледяная морось", 57: "Ледяная морось",
        61: "Дождь", 63: "Дождь", 65: "Дождь", 66: "Ледяной дождь",
        67: "Ледяной дождь", 71: "Снег", 73: "Снег", 75: "Снег",
        77: "Снежная крупа", 80: "Ливень", 81: "Ливень", 82: "Сильный ливень",
        85: "Снежный ливень", 86: "Снежный ливень", 95: "Гроза", 96: "Гроза", 99: "Гроза"
    };
    return codes.hasOwnProperty(code) ? codes[code] : "Погода";
}

// ---- 3. Утилиты ----

// Индекс текущего часа в hourly.time.
// Основной способ - сравнение строк с current_weather.time (одна и та же зона,
// один и тот же формат). Запасной - локальные часы контроллера.
function findCurrentHourIndex(times, currentTime) {
    var i;

    if (currentTime) {
        for (i = 0; i < times.length; i++) {
            if (times[i].substring(0, 13) === currentTime.substring(0, 13)) {
                return i;
            }
        }
    }

    var now = new Date();
    var prefix = now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" +
                 pad2(now.getDate()) + "T" + pad2(now.getHours());

    for (i = 0; i < times.length; i++) {
        if (times[i].substring(0, 13) === prefix) {
            return i;
        }
    }

    return -1;
}

// Срез массива фиксированной длины; недостающее и null -> null
function slice(arr, start, count) {
    var out = [];
    for (var i = 0; i < count; i++) {
        var idx = start + i;
        var v = (arr && idx < arr.length) ? arr[idx] : null;
        out.push(v === undefined ? null : v);
    }
    return out;
}

// Массив -> CSV. null становится пустым полем: "0.0,,1.2"
function toCsv(arr, decimals) {
    var out = [];
    for (var i = 0; i < arr.length; i++) {
        var v = arr[i];
        if (v === null) {
            out.push("");
        } else if (decimals > 0) {
            out.push(v.toFixed(decimals));
        } else {
            out.push("" + Math.round(v));
        }
    }
    return out.join(",");
}

function countFilled(arr) {
    var n = 0;
    for (var i = 0; i < arr.length; i++) {
        if (arr[i] !== null) n++;
    }
    return n;
}

// ---- 4. Обновление ----
function updateWeather() {
    var url = "https://api.open-meteo.com/v1/forecast?latitude=" + LAT +
              "&longitude=" + LON +
              "&current_weather=true" +
              "&hourly=temperature_2m,relativehumidity_2m,surface_pressure,windspeed_10m," +
              "weathercode,precipitation,precipitation_probability,cloudcover" +
              "&daily=sunrise,sunset" +
              "&forecast_days=2&timezone=auto";

    runShellCommand("curl -s --max-time 20 '" + url + "'", {
        captureOutput: true,
        exitCallback: function (exitCode, output) {
            if (exitCode !== 0 || !output) {
                log("Погода: ошибка запроса (код " + exitCode + ")");
                dev["weather/forecast_ok"] = false;
                return;
            }

            try {
                var data = JSON.parse(output);
                var current = data.current_weather;
                var hourly = data.hourly;

                if (!current || !hourly || !hourly.time || !hourly.temperature_2m) {
                    log("Погода: некорректный ответ API");
                    dev["weather/forecast_ok"] = false;
                    return;
                }

                var times = hourly.time;
                var start = findCurrentHourIndex(times, current.time);

                if (start < 0) {
                    log("Погода: текущий час не найден в прогнозе, данные не обновлены");
                    dev["weather/forecast_ok"] = false;
                    return;
                }

                // --- текущая погода ---
                dev["weather/temperature"] = current.temperature;
                dev["weather/windspeed"] = current.windspeed;
                dev["weather/description"] = getWeatherDescription(current.weathercode) +
                                             ", ветер " + current.windspeed + " км/ч";

                if (hourly.relativehumidity_2m && hourly.relativehumidity_2m[start] !== undefined) {
                    dev["weather/humidity"] = hourly.relativehumidity_2m[start];
                }
                if (hourly.surface_pressure && hourly.surface_pressure[start] !== undefined) {
                    dev["weather/pressure"] = Math.round(hourly.surface_pressure[start] * 0.750062);
                }

                dev["weather/last_update"] = new Date().toLocaleString();

                // --- срезы рядов ---
                var temps  = slice(hourly.temperature_2m, start, HOURS);
                var precip = slice(hourly.precipitation, start, HOURS);
                var prob   = slice(hourly.precipitation_probability, start, HOURS);
                var cloud  = slice(hourly.cloudcover, start, HOURS);
                var codes  = slice(hourly.weathercode, start, HOURS);

                // Сначала ряды, потом forecast_ok - панель ждёт флаг как признак
                // согласованного набора топиков.
                dev["weather/forecast_start"]     = times[start];
                dev["weather/temp_series"]        = toCsv(temps, 1);
                dev["weather/precip_series"]      = toCsv(precip, 1);
                dev["weather/precip_prob_series"] = toCsv(prob, 0);
                dev["weather/cloud_series"]       = toCsv(cloud, 0);
                dev["weather/code_series"]        = toCsv(codes, 0);

                if (PER_HOUR_CELLS) {
                    for (var o = 0; o < HOURS; o++) {
                        if (temps[o] !== null) {
                            dev["weather/temp_" + pad2(o)] = temps[o];
                        }
                    }
                }

                // --- агрегаты по температуре ---
                var tWindow = [];
                for (var i = 0; i < STAT_HOURS; i++) {
                    if (temps[i] !== null) tWindow.push(temps[i]);
                }
                if (tWindow.length === STAT_HOURS) {
                    dev["weather/temp_max_12h"] = Math.max.apply(null, tWindow);
                    dev["weather/temp_min_12h"] = Math.min.apply(null, tWindow);
                } else {
                    log("Погода: температура за " + tWindow.length + " ч из " +
                        STAT_HOURS + ", min/max не обновлены");
                }

                // --- агрегаты по осадкам ---
                var sum = 0, maxProb = 0, rainIn = -1;

                for (var j = 0; j < HOURS; j++) {
                    var mm = precip[j];
                    var pr = prob[j];

                    if (j < STAT_HOURS) {
                        if (mm !== null) sum += mm;
                        if (pr !== null && pr > maxProb) maxProb = pr;
                    }
                    if (rainIn < 0 && mm !== null && mm >= RAIN_MM) {
                        rainIn = j;
                    }
                }

                dev["weather/precip_now"] = (precip[0] === null) ? 0 : precip[0];
                dev["weather/precip_sum_12h"] = Math.round(sum * 10) / 10;
                dev["weather/precip_prob_max_12h"] = maxProb;
                dev["weather/rain_in"] = rainIn;
                dev["weather/rain_expected"] = (rainIn >= 0) || (maxProb >= RAIN_PROB);

                // --- восход/закат ---
                if (data.daily && data.daily.sunrise && data.daily.sunset) {
                    dev["weather/sunrise"] = data.daily.sunrise[0] || "";
                    dev["weather/sunset"]  = data.daily.sunset[0] || "";
                }

                var filled = countFilled(temps);
                dev["weather/forecast_ok"] = (filled === HOURS);

                log("Погода: " + current.temperature + "°C, часов " + filled + "/" + HOURS +
                    " с " + times[start] + ", осадки за 12ч " +
                    (Math.round(sum * 10) / 10) + " мм" +
                    (rainIn >= 0 ? ", начало через " + rainIn + " ч" : ""));

            } catch (e) {
                log("Погода: ошибка разбора: " + e.message);
                dev["weather/forecast_ok"] = false;
            }
        }
    });
}

// ---- 5. Обновление каждые 30 минут ----
defineRule("weather_update", {
    when: cron("*/30 * * * *"),
    then: updateWeather
});

// ---- 6. Первичный запуск ----
setTimeout(updateWeather, 3000);
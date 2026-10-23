"""Static botanical cutouts for the flower directory; no runtime generation."""

LEGACY_FLOWERS = {
    "Роза": "rose",
    "Пион": "peony",
    "Ромашка": "daisy",
    "Тюльпан": "tulip",
    "Гортензия": "hydrangea",
    "Лилия": "lily",
    "Калла": "calla",
    "Орхидея": "orchid",
    "Гвоздика": "carnation",
}

GENERATED_FLOWERS = {
    "Роза белая": "rose-white",
    "Роза красная": "rose-red",
    "Роза кустовая": "rose-spray",
    "Роза пионовидная": "rose-garden",
    "Хризантема": "chrysanthemum",
    "Хризантема кустовая": "chrysanthemum-spray",
    "Гвоздика кустовая": "carnation-spray",
    "Эустома": "eustoma",
    "Альстромерия": "alstroemeria",
    "Гербера": "gerbera",
    "Ранункулюс": "ranunculus",
    "Ирис": "iris",
    "Фрезия": "freesia",
    "Гипсофила": "gypsophila",
    "Василёк": "cornflower",
    "Маттиола": "matthiola",
    "Дельфиниум": "delphinium",
    "Антуриум": "anthurium",
    "Анемона": "anemone",
    "Нарцисс": "narcissus",
    "Гиацинт": "hyacinth",
    "Сирень": "lilac",
    "Подсолнух": "sunflower",
    "Гладиолус": "gladiolus",
    "Георгина": "dahlia",
    "Амариллис": "amaryllis",
    "Протея": "protea",
    "Стрелиция": "strelitzia",
    "Брассика": "brassica",
    "Вероника": "veronica",
    "Астильба": "astilbe",
    "Астранция": "astrantia",
    "Скабиоза": "scabiosa",
    "Озотамнус": "ozothamnus",
    "Лимониум": "limonium",
    "Солидаго": "solidago",
    "Бувардия": "bouvardia",
    "Ваксфлауэр": "waxflower",
    "Левкоспермум": "leucospermum",
    "Нерине": "nerine",
    "Агапантус": "agapanthus",
    "Мускари": "muscari",
    "Ландыш": "lily-of-the-valley",
    "Гибискус": "hibiscus",
}


def flower_image(name):
    if name in GENERATED_FLOWERS:
        return f"market/flowers/{GENERATED_FLOWERS[name]}.webp"
    if name in LEGACY_FLOWERS:
        return f"storefront/images/design/{LEGACY_FLOWERS[name]}.webp"
    return ""

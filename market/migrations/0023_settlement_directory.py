from django.db import migrations

# Rosstat, Cities of Moscow Oblast (74 cities):
# https://rosstat.gov.ru/storage/mediabank/Region_Goroda_2024.pdf
# Moscow locations remain separately selectable as used by address providers,
# including Moskovsky and Shcherbinka after their administrative reorganisation.
OBLAST = (
    "Апрелевка", "Балашиха", "Белоозёрский", "Бронницы", "Верея", "Видное",
    "Волоколамск", "Воскресенск", "Высоковск", "Голицыно", "Дедовск",
    "Дзержинский", "Дмитров", "Долгопрудный", "Домодедово", "Дрезна", "Дубна",
    "Егорьевск", "Жуковский", "Зарайск", "Звенигород", "Ивантеевка", "Истра",
    "Кашира", "Клин", "Коломна", "Королёв", "Котельники", "Красноармейск",
    "Красногорск", "Краснозаводск", "Краснознаменск", "Кубинка", "Куровское",
    "Ликино-Дулёво", "Лобня", "Лосино-Петровский", "Луховицы", "Лыткарино",
    "Люберцы", "Можайск", "Мытищи", "Наро-Фоминск", "Ногинск", "Одинцово",
    "Озёры", "Орехово-Зуево", "Павловский Посад", "Пересвет", "Подольск",
    "Протвино", "Пушкино", "Пущино", "Раменское", "Реутов", "Рошаль", "Руза",
    "Сергиев Посад", "Серпухов", "Солнечногорск", "Старая Купавна", "Ступино",
    "Талдом", "Фрязино", "Химки", "Хотьково", "Черноголовка", "Чехов",
    "Шатура", "Щёлково", "Электрогорск", "Электросталь", "Электроугли", "Яхрома",
)
MOSCOW = ("Москва", "Зеленоград", "Московский", "Троицк", "Щербинка")
LEGACY_SLUGS = {"Москва": "moscow", "Химки": "khimki", "Балашиха": "balashikha"}


def seed_cities(apps, schema_editor):
    cities = apps.get_model("market", "Settlement").objects.using(
        schema_editor.connection.alias
    )
    existing = {
        (row.region, row.name.casefold().replace("ё", "е")) for row in cities.all()
    }
    for region, names in (("77", MOSCOW), ("50", OBLAST)):
        for number, name in enumerate(names, 1):
            key = (region, name.casefold().replace("ё", "е"))
            if key in existing:
                continue
            slug = LEGACY_SLUGS.get(name, f"city-{region}-{number:03}")
            # A pre-existing unrelated custom slug must never be overwritten.
            while cities.filter(slug=slug).exists():
                slug += "-city"
            cities.create(name=name, region=region, slug=slug)
            existing.add(key)


class Migration(migrations.Migration):
    dependencies = [("market", "0022_enable_shop_delivery")]
    operations = [migrations.RunPython(seed_cities, migrations.RunPython.noop)]

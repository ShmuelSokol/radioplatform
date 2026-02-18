"""
1000+ Jewish music songs for seed data.
Generated from real Jewish music artists, albums, and song titles.
"""
import random

# Each artist: (name, [(album, [(title, duration, category), ...]), ...])
ARTISTS_CATALOG = [
    ("Yaakov Shwekey", [
        ("Kolot", [("Toda Raba", 234, "med_fast"), ("Vehi Sheamda", 310, "slow"), ("Am Yisrael Chai", 220, "lively"),
                   ("Shema Yisrael", 285, "slow"), ("Et Rekod", 198, "lively"), ("Meheira", 245, "med_fast"),
                   ("Im Eshkachech", 312, "slow"), ("Cry No More", 256, "med_fast")]),
        ("We Are A Miracle", [("Miracle", 243, "lively"), ("We Are A Miracle", 267, "lively"), ("Boruch Hashem", 224, "med_fast"),
                              ("Shomati", 299, "slow"), ("Those Were The Days", 234, "med_fast"), ("Lo Yaavod", 178, "lively")]),
        ("Leshem Shomayim", [("Leshem Shomayim", 281, "slow"), ("Mi Bon Siach", 344, "slow"), ("Kolenu", 207, "med_fast"),
                             ("Torah Hakdosha", 312, "slow"), ("V'haarev Na", 289, "slow")]),
        ("B'Chasdecha", [("B'Chasdecha", 267, "slow"), ("Ma Navu", 234, "med_fast"), ("Mama", 298, "slow"),
                         ("Asher Bara", 213, "lively"), ("V'Zakeini", 340, "slow")]),
    ]),
    ("Avraham Fried", [
        ("Classics Vol 1", [("Mama Rochel", 275, "slow"), ("Rachem", 295, "slow"), ("No Jew Will Be Left Behind", 243, "lively"),
                            ("Aleh Katan Sheli", 265, "slow"), ("Tanya", 312, "slow"), ("Didoh Bei", 198, "lively"),
                            ("Hu Yigal Osanu", 234, "med_fast"), ("Ki Onu Amecha", 267, "slow")]),
        ("Classics Vol 2", [("Chazak", 221, "lively"), ("Father Don't Cry", 287, "slow"), ("Tfillin", 198, "med_fast"),
                            ("The Dreamer", 312, "slow"), ("Shtar HaTno'im", 245, "med_fast"), ("Nisim", 178, "lively")]),
        ("Ah Mechaieh", [("Ah Mechaieh", 234, "lively"), ("Good Yom Tov", 178, "lively"), ("Abba", 298, "slow"),
                         ("Boi Kala", 267, "slow"), ("Hinei Mah Tov", 198, "lively"), ("Shiras HaBaal Shem", 312, "slow")]),
        ("Yankel Yankel", [("Yankel Yankel", 256, "med_fast"), ("Kol Haderech", 287, "slow"), ("V'Taher Libeinu", 234, "med_fast"),
                           ("Zehr Gezunt", 198, "lively"), ("Eishes Chayil", 310, "slow")]),
        ("Keep Climbing", [("Keep Climbing", 243, "lively"), ("Yesh Tikvah", 278, "med_fast"), ("Sheyibaneh", 312, "slow"),
                           ("Besiyata Dishmaya", 198, "med_fast"), ("Avinu Malkeinu", 334, "slow")]),
    ]),
    ("MBD", [
        ("Greatest Hits Vol 1", [("Yidden", 285, "lively"), ("Just One Shabbos", 298, "slow"), ("Moshiach", 220, "lively"),
                                 ("Ribono Shel Olam", 340, "slow"), ("Someday", 265, "med_fast"), ("V'hi Sh'amda", 312, "slow"),
                                 ("Ani Maamin", 287, "slow"), ("Ko Amar", 198, "lively")]),
        ("Greatest Hits Vol 2", [("Let My People Go", 245, "lively"), ("Bilvavi", 310, "slow"), ("Memories", 267, "slow"),
                                 ("Vizhnitz Medley", 345, "lively"), ("Hinei Yomim Ba'im", 312, "slow"),
                                 ("Father Hear Our Prayer", 287, "slow")]),
        ("V'Shomru", [("V'Shomru", 310, "shabbos"), ("Modim", 234, "slow"), ("Mashiv Haruach", 198, "med_fast"),
                      ("Retzei", 267, "shabbos"), ("B'fi Yeshorim", 287, "slow"), ("Yism'chu", 221, "shabbos")]),
        ("Kulam Ahuvim", [("Kulam Ahuvim", 254, "med_fast"), ("Tzaddik Katamar", 289, "slow"), ("B'Tzeis Yisrael", 234, "lively"),
                          ("Lev Tahor", 312, "slow"), ("Borchi Nafshi", 265, "med_fast")]),
    ]),
    ("Benny Friedman", [
        ("Kulanu Nelech", [("Kol Berama", 263, "med_fast"), ("Kulanu Nelech", 234, "lively"), ("Ivdu", 198, "lively"),
                           ("Toda", 267, "med_fast"), ("Yesh Tikvah", 287, "slow"), ("Mi Adir", 210, "lively")]),
        ("B'Yachad", [("B'Yachad", 245, "lively"), ("Acheinu", 312, "slow"), ("Na'ar Hayiti", 267, "med_fast"),
                      ("Fill The World With Light", 234, "med_fast"), ("Kah Echsof", 310, "slow")]),
        ("Light", [("Ashira Lashem", 246, "relax"), ("Light", 298, "slow"), ("Torah", 234, "med_fast"),
                   ("Besheim Hashem", 267, "lively"), ("Hiskabtzi", 221, "med_fast"), ("Yaaleh V'Yavo", 287, "slow")]),
        ("Maaleh", [("Bum Bum Bum", 198, "lively"), ("Maaleh", 278, "slow"), ("Toda L'Kel", 245, "med_fast"),
                    ("Sh'ma Koleinu", 310, "slow"), ("Rak Simcha", 189, "lively")]),
    ]),
    ("8th Day", [
        ("Chasing Prophecy", [("Ya'alili", 230, "lively"), ("Hooleh", 198, "lively"), ("Chasing Prophecy", 267, "med_fast"),
                              ("Bless The Children", 287, "slow"), ("Rock The World", 213, "lively"),
                              ("Avraham", 245, "med_fast"), ("Brooklyn", 198, "lively")]),
        ("Inner Flame", [("Inner Flame", 256, "med_fast"), ("Be Strong", 234, "lively"), ("All You Got", 198, "lively"),
                         ("This World", 267, "slow"), ("Higher & Higher", 178, "lively"), ("Simple Man", 312, "slow")]),
        ("Celebrate", [("Celebrate", 198, "lively"), ("Ivri Anochi", 230, "lively"), ("Tov L'Hodos", 267, "med_fast"),
                       ("Shiru Lo", 221, "lively"), ("Rise Up", 245, "lively")]),
    ]),
    ("Lipa Schmeltzer", [
        ("A Poshiter Yid", [("A Poshiter Yid", 198, "lively"), ("Abi Me'lebt", 234, "med_fast"), ("Mizrach", 267, "lively"),
                            ("Gelt", 178, "lively"), ("Hentelach", 256, "med_fast"), ("Torah Hi", 312, "slow")]),
        ("Lipa In The Chasuna", [("L'chu Neranena", 198, "lively"), ("Hora", 167, "lively"), ("Chasuna Waltz", 312, "slow"),
                                 ("Simchas Chosson V'Kala", 234, "lively"), ("Badchan Intro", 89, "lively"),
                                 ("Mechutonim Dance", 245, "lively")]),
        ("Wake Up", [("Wake Up", 198, "lively"), ("Jumping", 178, "lively"), ("Rabi Shimon", 267, "slow"),
                     ("Modeh Ani", 234, "med_fast"), ("B'Simcha", 198, "lively")]),
    ]),
    ("Ohad Moskowitz", [
        ("B'Siyata Dishmaya", [("Aneinu", 240, "med_fast"), ("B'Siyata Dishmaya", 312, "slow"), ("Hinei Lo Yanum", 267, "slow"),
                               ("Yismechu", 198, "lively"), ("Ke'ayal", 287, "slow"), ("Tefila L'Ani", 310, "slow")]),
        ("Rabos Machshavos", [("Rabos Machshavos", 298, "slow"), ("Lo Ira", 234, "med_fast"), ("Al Tira", 267, "slow"),
                              ("Bitchu B'Hashem", 221, "med_fast"), ("Ki Imcha", 310, "slow")]),
    ]),
    ("Ishay Ribo", [
        ("Elecha", [("Seder HaAvoda", 298, "slow"), ("Elecha", 345, "slow"), ("Simanim", 267, "med_fast"),
                    ("Lashuv Habayta", 234, "slow"), ("Halev Sheli", 310, "slow"), ("Keter Melucha", 320, "slow"),
                    ("Sibat HaSibot", 287, "slow")]),
        ("Nafshi", [("Nafshi", 278, "slow"), ("Im Tirtzi", 234, "med_fast"), ("Hine Ma Tov", 198, "lively"),
                    ("B'Tzelem Elokim", 310, "slow"), ("Levakesh Rachamim", 265, "slow")]),
        ("Yamim Nora'im", [("Hayom Harat Olam", 312, "slow"), ("Unesaneh Tokef", 345, "slow"), ("Avinu Malkeinu", 289, "slow"),
                           ("Ki Hinei Ka'Chomer", 267, "slow"), ("B'Rosh Hashana", 298, "slow")]),
    ]),
    ("Shlomo Carlebach", [
        ("Holy Brothers & Sisters", [("Am Yisrael Chai", 198, "lively"), ("V'Haer Eineinu", 312, "slow"),
                                     ("Pischu Li", 178, "lively"), ("Od Yishama", 234, "lively"),
                                     ("Hashem Oz", 267, "lively"), ("Esa Einai", 310, "slow")]),
        ("Nachamu Nachamu Ami", [("Nachamu", 345, "slow"), ("V'Sham Nashir", 267, "slow"), ("Borchu", 198, "lively"),
                                 ("Ki Va Moed", 234, "med_fast"), ("Torah Orah", 287, "slow")]),
        ("Days Are Coming", [("Days Are Coming", 267, "lively"), ("Lema'an Achai", 198, "lively"),
                             ("Return Again", 312, "slow"), ("Shir Hashirim", 345, "slow"),
                             ("David Melech Yisrael", 167, "lively"), ("L'maancha", 278, "slow")]),
    ]),
    ("Beri Weber", [
        ("B'ezras Hashem", [("Modeh Ani", 215, "lively"), ("Hashkiveinu", 310, "slow"), ("Ata Takum", 234, "med_fast"),
                            ("B'ezras Hashem", 267, "slow"), ("Keil Adon", 198, "lively")]),
        ("Neshama", [("Neshama", 287, "slow"), ("Rak B'Yisrael", 198, "lively"), ("Al Naharos Bavel", 310, "slow"),
                     ("V'Nomar Lefanav", 234, "lively"), ("Lev Echad", 245, "med_fast")]),
    ]),
    ("Simcha Leiner", [
        ("V'Ani Tefillasi", [("V'Ani Tefillasi", 312, "slow"), ("Birchas Habayis", 234, "med_fast"),
                             ("Yishtabach", 287, "slow"), ("V'Hu Keili", 267, "slow"), ("Sim Shalom", 245, "slow")]),
        ("The Hits", [("Mi Haish", 198, "lively"), ("Chasdei Hashem", 234, "med_fast"), ("Shiru Lamelech", 267, "lively"),
                      ("Ki Ata Imadi", 310, "slow"), ("Shir La'Ma'alos", 221, "med_fast")]),
    ]),
    ("Motty Steinmetz", [
        ("Haneshama", [("Haneshama Sheli", 298, "slow"), ("Rachamana", 234, "slow"), ("Kol Mekadesh", 267, "shabbos"),
                       ("Sim Shalom", 310, "slow"), ("B'Motzaei Menucha", 198, "shabbos")]),
        ("Lev", [("Lev", 245, "slow"), ("Al Tashlicheini", 312, "slow"), ("Uv'Nucho Yomar", 198, "med_fast"),
                 ("Mi She'asa Nisim", 267, "lively"), ("Havdalah", 178, "shabbos")]),
    ]),
    ("Eitan Katz", [
        ("L'Ma'ancha", [("L'Ma'ancha", 312, "slow"), ("Libi Bamizrach", 298, "slow"), ("V'Shamru", 267, "shabbos"),
                        ("B'Shem Hashem", 234, "med_fast"), ("Aneinu", 287, "slow")]),
        ("Unplugged", [("Adon Olam", 198, "slow"), ("Shema Koleinu", 310, "slow"), ("Essa Einai", 234, "slow"),
                       ("Hamalach", 267, "slow"), ("Ki V'Simcha", 221, "lively")]),
    ]),
    ("Baruch Levine", [
        ("Peduscha", [("V'zakeini", 298, "slow"), ("Hamalach", 267, "slow"), ("Peduscha", 310, "slow"),
                      ("B'tzeis Yisrael", 234, "lively"), ("P'sach Libi", 245, "med_fast")]),
        ("Modim", [("Modim", 234, "slow"), ("Vizakeini", 312, "slow"), ("B'nei Heichala", 267, "shabbos"),
                   ("Tov L'hodos", 198, "med_fast"), ("Echad Mi Yodeya", 221, "lively")]),
    ]),
    ("Yonatan Razel", [
        ("Katonti", [("Katonti", 312, "slow"), ("V'Hi She'amda", 345, "slow"), ("B'Yom Chasunaso", 267, "slow"),
                     ("Shma Bni", 234, "slow"), ("Kol B'Rama", 298, "slow")]),
        ("Sach Hakol", [("Sach Hakol", 287, "slow"), ("Mizmor Shir", 234, "med_fast"), ("Nachpesa", 312, "slow"),
                        ("Shir Lamaalos", 198, "slow"), ("Yehi Ratzon", 267, "slow")]),
    ]),
    ("Dovid Gabay", [
        ("Le'Duvid", [("Le'Duvid", 234, "med_fast"), ("Ashrei", 267, "lively"), ("Rachamana", 310, "slow"),
                      ("Ki Eshm'ra Shabbos", 287, "shabbos"), ("Havdalah", 198, "shabbos")]),
        ("Omar Dovid", [("Omar Dovid", 245, "med_fast"), ("Mah Tovu", 267, "slow"), ("Al Tira", 234, "slow"),
                        ("V'Al Kulam", 312, "slow"), ("B'Tzel Kanfecha", 198, "slow")]),
    ]),
    ("Zusha", [
        ("Mashiach", [("Mashiach", 267, "lively"), ("Bar Yochai", 234, "lively"), ("Kavei", 198, "lively"),
                      ("Yai Dai Dai", 178, "lively"), ("It's Shabbos!", 221, "shabbos"), ("Zachreini", 287, "slow")]),
        ("When The Heart Cries", [("When The Heart Cries", 310, "slow"), ("Ohr Chadash", 234, "med_fast"),
                                  ("Ribboinoi", 267, "slow"), ("L'Dovid", 198, "slow"), ("City Of Light", 245, "med_fast")]),
    ]),
    ("Shalsheles", [
        ("Shalsheles 1", [("Esa Einai", 287, "slow"), ("Shomer Yisrael", 310, "slow"), ("V'Lirushalayim", 234, "med_fast"),
                          ("Adon Olam", 267, "slow"), ("Ana B'Ko'ach", 198, "med_fast"), ("Baruch Hashem", 245, "lively")]),
        ("Shalsheles 2", [("Omar Rabbi Akiva", 312, "slow"), ("Mi Ha'Ish", 198, "lively"), ("B'Shuv Hashem", 267, "slow"),
                          ("R'Tzei", 234, "shabbos"), ("K'Rachem Av", 310, "slow")]),
    ]),
    ("Shloime Taussig", [
        ("Ah Yid", [("Im Omarti", 184, "med_fast"), ("Ah Yid", 198, "lively"), ("V'Ya'azor", 267, "slow"),
                    ("Shema B'ni", 312, "slow"), ("Kol B'Seder", 234, "med_fast")]),
        ("V'Chol Ma'aminim", [("V'Chol Ma'aminim", 310, "slow"), ("Shabbos Kodesh", 267, "shabbos"), ("Kesher Shel Kayama", 245, "med_fast"),
                              ("Lo Bashamayim Hi", 234, "lively"), ("Ana Avda", 287, "slow")]),
    ]),
    ("Ari Goldwag", [
        ("Soul 5", [("Lo Nafsik Lirkod", 258, "med_fast"), ("Am Echad", 234, "lively"), ("B'Yado", 198, "med_fast"),
                    ("Shema", 267, "slow"), ("Hashiveinu", 310, "slow"), ("K'She'halev Bocheh", 287, "slow")]),
        ("Am Echad", [("Am Echad", 198, "lively"), ("Lishmor Al HaOlam", 267, "med_fast"), ("Malachim", 234, "slow"),
                      ("Y'varech'cha", 287, "slow"), ("B'Sha'a Tova", 198, "lively")]),
    ]),
    ("Yeedle", [
        ("Say Asay", [("Say Asay", 198, "lively"), ("Vayehi Binsoa", 267, "slow"), ("Shabbos", 310, "shabbos"),
                      ("Am Hanetzach", 234, "lively"), ("Torah", 287, "slow")]),
        ("Kol Yisrael", [("Kol Yisrael", 245, "lively"), ("Ki Hinei", 198, "slow"), ("Mashiach", 234, "lively"),
                         ("Yerushalayim", 312, "slow"), ("Hineni", 267, "slow")]),
    ]),
    ("Shloimy Gertner", [
        ("Neshamale", [("Neshamale", 312, "slow"), ("Abba", 287, "slow"), ("Ashreichem", 234, "med_fast"),
                       ("Bayom Hahu", 267, "slow"), ("Ilu Finu", 198, "med_fast")]),
        ("Yisrael", [("Yisrael", 245, "lively"), ("Kah Ribon", 310, "shabbos"), ("Ata Gibor", 198, "lively"),
                     ("V'Shamru", 267, "shabbos"), ("Tzama", 234, "slow")]),
    ]),
    ("Levy Falkowitz", [
        ("Mi K'Amcha", [("Mi K'Amcha", 234, "lively"), ("Shabbos Medley", 312, "shabbos"), ("Sh'ma Koleinu", 267, "slow"),
                        ("Birchas Kohanim", 298, "slow"), ("Im Hashem Lo Yivneh", 198, "med_fast")]),
    ]),
    ("Gad Elbaz", [
        ("Hashem Melech", [("Hashem Melech", 198, "lively"), ("Ana BeKo'ach", 234, "slow"), ("Elohim", 267, "slow"),
                           ("Shema Yisrael", 287, "slow"), ("Od Yavo Shalom", 198, "lively"), ("Yerushalayim Shel Zahav", 310, "slow")]),
        ("Light Out Of Darkness", [("Or Min Ha'Choshech", 245, "med_fast"), ("Tamid", 198, "lively"), ("Kol HaOlam Kulo", 234, "lively"),
                                   ("Neshama", 312, "slow"), ("Ha'Or She'Becha", 267, "slow")]),
    ]),
    ("Abie Rotenberg", [
        ("Journeys 1", [("The Place Where I Belong", 312, "slow"), ("Joe DiMaggio Card", 267, "slow"),
                        ("Mama's Lullaby", 287, "slow"), ("Memories", 345, "slow"), ("The Tailor", 310, "slow")]),
        ("Journeys 2", [("Who Am I", 298, "slow"), ("The Letter", 267, "slow"), ("Conversation in the Womb", 312, "slow"),
                        ("Shmelkie's Niggun", 234, "slow"), ("Habein Yakir Li", 287, "slow")]),
        ("D'veykus 1", [("Esa Einai", 234, "slow"), ("Yedid Nefesh", 310, "slow"), ("Mizmor L'Dovid", 198, "med_fast"),
                        ("V'Zakeini", 267, "slow"), ("Shabbos HaMalka", 312, "shabbos")]),
        ("Aish", [("Aish", 198, "lively"), ("Am Yisrael", 234, "lively"), ("No", 167, "lively"),
                  ("Acheinu", 287, "slow"), ("Shiras Ha'Yam", 245, "lively")]),
    ]),
    ("Six13", [
        ("Six13 Vol 1", [("Chanukah Rock", 198, "lively"), ("Shabbat Medley", 234, "shabbos"), ("Am Echad", 178, "lively"),
                         ("V'Shamru", 267, "shabbos"), ("Hallelu", 198, "lively")]),
        ("Six13 Vol 2", [("Purim Parody", 234, "purim"), ("Pesach Seder Rap", 198, "lively"), ("L'Shana Haba'a", 267, "slow"),
                         ("Shir Hashirim", 312, "slow"), ("Sefira", 245, "slow")]),
    ]),
    ("Maccabeats", [
        ("Out Of The Box", [("Candlelight", 234, "lively"), ("One Day", 198, "lively"), ("Purim Song", 178, "purim"),
                            ("Book of Good Life", 267, "med_fast"), ("Miracle", 245, "lively")]),
        ("Voices", [("Home", 234, "slow"), ("Blessed", 198, "med_fast"), ("Shabbos", 267, "shabbos"),
                    ("Light", 178, "lively"), ("I Believe", 287, "slow")]),
    ]),
    ("Shea Berko", [
        ("Echad", [("Echad", 234, "lively"), ("Shabbos Queen", 287, "shabbos"), ("Kol Nidrei", 312, "slow"),
                   ("V'Yiten L'Cha", 198, "shabbos"), ("Boi B'Shalom", 267, "shabbos")]),
    ]),
    ("Michoel Schnitzler", [
        ("Der Rebbe Zingt", [("Der Rebbe Zingt", 267, "lively"), ("Shalosh Regalim", 234, "lively"),
                             ("Tatte", 312, "slow"), ("Shabbos Waltz", 287, "shabbos"),
                             ("Cheder Yingel", 198, "lively"), ("Mein Shtetle", 345, "slow")]),
    ]),
    ("Mendy Werzberger", [
        ("Hamavdil", [("Hamavdil", 198, "shabbos"), ("Yibaneh Hamikdash", 234, "lively"), ("Kol Dodi", 267, "slow"),
                      ("Tzion", 310, "slow"), ("Shabbos Nachamu", 287, "shabbos")]),
    ]),
    ("Shloime Daskal", [
        ("Mi Adir", [("Mi Adir", 198, "lively"), ("Borchi Nafshi", 267, "slow"), ("Mimkomcha", 310, "slow"),
                     ("V'Ahavta", 234, "med_fast"), ("Anim Zmiros", 287, "slow"), ("L'Cha Dodi", 267, "shabbos")]),
    ]),
    ("Eli Marcus", [
        ("Heartstrings", [("Heartstrings", 312, "slow"), ("Modeh Ani", 198, "lively"), ("V'Nomar", 234, "med_fast"),
                          ("Acheinu", 267, "slow"), ("Kumzitz", 345, "slow")]),
    ]),
    ("Pinny Schachter", [
        ("Hineni", [("Hineni", 287, "slow"), ("Shir HaMaalos", 234, "slow"), ("Kaili Kaili", 198, "slow"),
                    ("Hashkiveinu", 310, "slow"), ("B'Tzeit Yisrael", 267, "lively")]),
    ]),
    ("Yossi Green", [
        ("Composed", [("Kol B'Rama", 312, "slow"), ("V'Chol Ma'aminim", 287, "slow"), ("Shalom Aleichem", 234, "shabbos"),
                      ("Omar Hashem L'Yaakov", 267, "slow"), ("Tov Lehodos", 198, "med_fast")]),
    ]),
    ("Dedi Graucher", [
        ("B'nei Heichala", [("B'nei Heichala", 267, "shabbos"), ("Al Kol Eileh", 198, "med_fast"), ("HaKol Yoducha", 234, "lively"),
                            ("Lo Amus", 287, "slow"), ("Chamol", 312, "slow")]),
    ]),
    ("TYH Boys", [
        ("Kumzitz", [("Billionaire", 267, "med_fast"), ("Shalom Aleichem", 312, "shabbos"), ("Am Echad", 198, "lively"),
                     ("Lechu Neranena", 234, "lively"), ("Atah Echad", 287, "slow")]),
    ]),
    ("The Chevra", [
        ("Halo", [("Halo", 198, "lively"), ("Omar Rabbi Elazar", 312, "slow"), ("Hein Am", 234, "med_fast"),
                  ("V'Taher", 267, "slow"), ("Shabbos Is Coming", 178, "shabbos")]),
    ]),
    ("Lev Tahor", [
        ("Lev Tahor 1", [("Mah Nishtana", 198, "lively"), ("B'Tzeis", 234, "lively"), ("Adon Olam", 267, "slow"),
                         ("Shabbos Melody", 287, "shabbos"), ("Ana Hashem", 310, "slow")]),
    ]),
    ("Yishai Lapidot", [
        ("Ratziti", [("Ratziti Sheti'da'i", 234, "slow"), ("Livnot Bayit", 267, "slow"), ("Ani V'Ata", 198, "lively"),
                     ("Chabibi", 187, "lively"), ("Torah Yevakesh", 310, "slow")]),
    ]),
    ("Aaron Razel", [
        ("Mizmor Shir", [("Mizmor Shir", 312, "slow"), ("V'Hi She'amda", 267, "slow"), ("Omar Hashem", 234, "slow"),
                         ("Pitchu Li", 198, "lively"), ("Nachamu Ami", 287, "slow")]),
    ]),
    ("Avremi Roth", [
        ("Yiddishe Niggunim", [("Rebbe Rebbe", 267, "lively"), ("Simchas Torah Medley", 345, "lively"),
                               ("Shabbos Waltz", 312, "shabbos"), ("Chupah March", 287, "slow"),
                               ("Tisch Niggun", 198, "lively"), ("Yom Tov Medley", 310, "lively")]),
    ]),
    ("Camp Sternberg", [
        ("Camp Sternberg Vol 1", [("Hello Summer", 198, "lively"), ("Tatte", 312, "slow"), ("Neshama", 267, "slow"),
                                  ("Achdus", 234, "lively"), ("Staff Play Theme", 178, "lively")]),
    ]),
    ("Yossi Azulay", [
        ("Sephardic Soul", [("Ana B'Koach", 234, "slow"), ("Yigdal", 198, "med_fast"), ("Ki Eshmera", 267, "shabbos"),
                            ("Lecha Dodi", 310, "shabbos"), ("V'Shamru", 234, "shabbos"), ("Tzur Mishelo", 198, "shabbos")]),
    ]),
    ("Mordechai Shapiro", [
        ("Machar", [("Machar", 198, "lively"), ("Hakol Mishamayim", 267, "med_fast"), ("Schar Mitzvah", 234, "lively"),
                    ("V'Zakeini", 310, "slow"), ("Adon Olam", 198, "slow")]),
    ]),
    ("Shmueli Ungar", [
        ("Shmueli", [("Mach A Bracha", 198, "lively"), ("Mimkomo", 267, "slow"), ("Yachad", 234, "lively"),
                     ("Hashkiveinu", 310, "slow"), ("Kol Rinah", 198, "lively")]),
    ]),
]

# Additional songs to fill to 1000+ — common niggunim, wedding songs, Shabbos zemiros
EXTRA_SONGS = [
    # Shabbos Zemiros
    ("Traditional", "Shabbos Zemiros", [
        ("Shalom Aleichem", 198, "shabbos"), ("Eishes Chayil", 234, "shabbos"), ("Kol Mekadesh", 267, "shabbos"),
        ("Menucha V'Simcha", 198, "shabbos"), ("Mah Yedidus", 287, "shabbos"), ("Yom Zeh L'Yisrael", 234, "shabbos"),
        ("Baruch Keil Elyon", 198, "shabbos"), ("Kah Ribbon Olam", 312, "shabbos"), ("Yom Shabason", 234, "shabbos"),
        ("D'ror Yikra", 267, "shabbos"), ("Tzur Mishelo", 198, "shabbos"), ("Adon Olam", 178, "shabbos"),
        ("Yigdal", 210, "shabbos"), ("B'Motza'ei Menucha", 198, "shabbos"),
    ]),
    # Classic Niggunim
    ("Traditional", "Classic Niggunim", [
        ("Lubavitch Niggun 1", 312, "slow"), ("Lubavitch Niggun 2", 267, "slow"), ("Breslov Niggun", 345, "lively"),
        ("Modzitz Waltz", 287, "slow"), ("Vizhnitz Hora", 198, "lively"), ("Belz Niggun", 267, "slow"),
        ("Satmar Niggun", 234, "slow"), ("Bobov March", 198, "lively"), ("Karlin Niggun", 310, "slow"),
        ("Ger Niggun", 178, "lively"), ("Toldos Aharon Tish", 345, "slow"), ("Skulen Niggun", 312, "slow"),
        ("Chabad Slow Niggun", 398, "slow"), ("Simchas Torah Hakafos", 198, "lively"), ("Tish Niggun", 267, "slow"),
    ]),
    # Wedding Classics
    ("Various Artists", "Wedding Classics", [
        ("Chosson Kallah Mazal Tov", 198, "lively"), ("Od Yishama", 178, "lively"), ("Siman Tov U'Mazal Tov", 167, "lively"),
        ("Im Eshkachech", 312, "slow"), ("Mi Bon Siach", 287, "slow"), ("Mi Adir", 198, "lively"),
        ("Chassidishe Wedding March", 234, "slow"), ("Badeken Niggun", 310, "slow"), ("Chupah Melody", 345, "slow"),
        ("Mezinka", 178, "lively"), ("Second Dance Medley", 267, "lively"), ("Hora", 198, "lively"),
        ("Slow Dance Set", 312, "slow"), ("Leibedik Set", 198, "lively"), ("Grand March", 267, "lively"),
    ]),
    # Hallel & Tefila
    ("Various Artists", "Hallel V'Zimra", [
        ("Hallel Complete", 450, "lively"), ("Hodu Lashem Ki Tov", 198, "lively"), ("Ana Hashem Hoshia Na", 234, "med_fast"),
        ("Min HaMeitzar", 267, "slow"), ("Pitchu Li", 198, "lively"), ("Odcha Ki Anisani", 234, "med_fast"),
        ("Zeh HaYom", 178, "lively"), ("Hodu Part 2", 267, "lively"), ("B'Tzeis Yisrael", 234, "lively"),
        ("Hallelukah", 198, "lively"),
    ]),
    # Purim
    ("Various Artists", "Purim Collection", [
        ("Mishenichnas Adar", 198, "purim"), ("LaYehudim Haysa Orah", 178, "purim"), ("Chayav Inish", 167, "purim"),
        ("Al HaNisim", 234, "purim"), ("V'Nahafoch Hu", 198, "purim"), ("Shoshanas Yaakov", 267, "purim"),
        ("Utzu Eitza", 198, "purim"), ("Kacha Ya'aseh", 234, "purim"), ("Purim Medley", 312, "purim"),
        ("Ani Purim", 178, "purim"),
    ]),
    # Chanukah
    ("Various Artists", "Chanukah Collection", [
        ("Maoz Tzur", 198, "lively"), ("Al HaNisim", 234, "lively"), ("Haneiros Halalu", 178, "slow"),
        ("Mi Y'malel", 198, "lively"), ("Chanukah Oh Chanukah", 167, "lively"), ("S'vivon", 198, "lively"),
        ("Banu Choshech", 234, "lively"), ("Ner Li", 178, "lively"),
    ]),
    # Yomim Noraim
    ("Various Artists", "Yomim Noraim", [
        ("Avinu Malkeinu", 345, "slow"), ("Unesaneh Tokef", 398, "slow"), ("Kol Nidrei", 312, "slow"),
        ("Hineni He'Ani", 287, "slow"), ("V'Chol Ma'aminim", 267, "slow"), ("Hayom T'amtzeinu", 234, "slow"),
        ("L'Shanah Haba'a", 198, "slow"), ("Shema Koleinu", 312, "slow"), ("V'Al Cheit", 267, "slow"),
        ("Mareh Kohen", 345, "slow"),
    ]),
    # Modern Israeli
    ("Various Israeli", "Israeli Hits", [
        ("Od Yavo Shalom Aleinu", 198, "lively"), ("Hevenu Shalom Aleichem", 167, "lively"),
        ("Yerushalayim Shel Zahav", 310, "slow"), ("Lu Yehi", 287, "slow"), ("Bashana Haba'a", 234, "med_fast"),
        ("Hallelujah", 198, "lively"), ("Kan", 267, "slow"), ("Ein Li Eretz Acheret", 245, "slow"),
        ("Al Kol Eleh", 234, "slow"), ("Chai", 198, "lively"), ("Hatikvah", 178, "slow"),
        ("Im Tirtzu", 234, "lively"), ("Kachol V'Lavan", 198, "lively"), ("Y'rushalayim", 287, "slow"),
    ]),
]


def generate_songs():
    """Generate all songs from the catalog. Returns list of dicts."""
    songs = []

    # From main artist catalog
    for artist, albums in ARTISTS_CATALOG:
        for album, tracks in albums:
            for title, duration, category in tracks:
                songs.append({
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "duration": float(duration),
                    "category": category,
                })

    # From extra collections
    for artist, album, tracks in EXTRA_SONGS:
        for title, duration, category in tracks:
            songs.append({
                "title": title,
                "artist": artist,
                "album": album,
                "duration": float(duration),
                "category": category,
            })

    # Generate additional variations to ensure we reach 1000+
    # Add "Live" and "Acoustic" versions of popular songs
    base_count = len(songs)
    variations = []
    suffixes = [
        ("Live", lambda d: d + random.randint(20, 60)),
        ("Acoustic", lambda d: d + random.randint(-20, 30)),
        ("Remix", lambda d: int(d * 0.85)),
        ("Extended", lambda d: d + random.randint(40, 90)),
        ("Acapella", lambda d: int(d * 0.9)),
    ]
    random.seed(613)  # Deterministic
    idx = 0
    while base_count + len(variations) < 1020:
        song = songs[idx % base_count]
        suffix_name, dur_fn = suffixes[idx % len(suffixes)]
        variations.append({
            "title": f"{song['title']} ({suffix_name})",
            "artist": song["artist"],
            "album": f"{song['album']} — {suffix_name}" if song["album"] else suffix_name,
            "duration": float(max(60, dur_fn(int(song["duration"])))),
            "category": song["category"],
        })
        idx += 1

    songs.extend(variations)
    return songs

"""出典のある参照データ。

`seed.py` の相場（手書きの概算値）とは違い、**こちらは出典のある実データ**。
混ぜないようにファイルを分けている。

出典
- 空き家活用の事例: 自治体通信オンライン
  https://jichitai.works/articles/3296
- 解体費用の相場: スッキリ解体
  https://sukkiri-kaitai.com/kaitai-hiyou/kaitaihiyo-mokuzo/
  https://sukkiri-kaitai.com/kaitaikoujigyousya/
  （あんしん解体業者認定協会の 2020〜2024 年・30,000 件以上の解体工事データが元）
"""

from __future__ import annotations

import sqlite3

USECASE_SOURCE_NAME = "自治体通信オンライン"
USECASE_SOURCE_URL = "https://jichitai.works/articles/3296"

DEMOLITION_SOURCE_NAME = "スッキリ解体"
DEMOLITION_SOURCE_URL = "https://sukkiri-kaitai.com/kaitai-hiyou/kaitaihiyo-mokuzo/"
DEMOLITION_CONTRACTOR_URL = "https://sukkiri-kaitai.com/kaitaikoujigyousya/"

# --- 都道府県 → 地方区分 ---------------------------------------------------
# 解体費用の相場が「地方」単位で公表されているため、その区分に合わせる。
PREFECTURE_REGIONS: dict[str, str] = {
    **{
        p: "北海道・東北"
        for p in ("北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県")
    },
    **{
        p: "関東"
        for p in ("茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県")
    },
    **{
        p: "中部"
        for p in (
            "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
            "岐阜県", "静岡県", "愛知県",
        )
    },
    **{
        p: "近畿"
        for p in ("三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県")
    },
    **{
        p: "中国・四国"
        for p in (
            "鳥取県", "島根県", "岡山県", "広島県", "山口県",
            "徳島県", "香川県", "愛媛県", "高知県",
        )
    },
    **{
        p: "九州・沖縄"
        for p in (
            "福岡県", "佐賀県", "長崎県", "熊本県", "大分県",
            "宮崎県", "鹿児島県", "沖縄県",
        )
    },
}

# --- 解体費用 ---------------------------------------------------------------
# 木造の地方別坪単価（円/坪）。出典に掲載されている実測値。
WOOD_COST_BY_REGION: dict[str, int] = {
    "関東": 35_270,
    "中部": 32_042,
    "近畿": 36_185,
    "北海道・東北": 33_460,
    "中国・四国": 29_038,
    "九州・沖縄": 30_099,
}
WOOD_COST_NATIONAL = 34_090

# 構造別の全国平均坪単価（円/坪）。出典に掲載されている実測値。
COST_BY_STRUCTURE: dict[str, int] = {
    "木造": 34_090,
    "軽量鉄骨造": 38_917,
    "重量鉄骨造": 49_102,
    "鉄筋コンクリート造": 62_465,
    "その他": 34_090,  # 不明なら木造相当で見積もる
}


def demolition_rows() -> list[tuple[str, str, int]]:
    """(地方, 構造, 坪単価) を組み立てる。

    出典が公表しているのは「木造の地方別」と「構造別の全国平均」の 2 つ。
    地方 × 構造の表は無いので、木造の地域差を比率として他の構造に掛けて**導出**する。
    導出値であることは docs/DATA_SOURCES.md に明記すること。
    """
    rows: list[tuple[str, str, int]] = []
    for region, wood_cost in WOOD_COST_BY_REGION.items():
        ratio = wood_cost / WOOD_COST_NATIONAL
        for structure, national in COST_BY_STRUCTURE.items():
            if structure in ("木造", "その他"):
                # 出典の実測値をそのまま使う（丸めない）
                rows.append((region, structure, wood_cost))
                continue
            # 導出値なので 100 円単位に丸めて、実測値と見分けが付くようにする
            rows.append((region, structure, int(round(national * ratio / 100) * 100)))
    return rows


# --- 空き家活用の事例 -------------------------------------------------------
# (都道府県, 事例名, 分類, 概要, 数値・実績)
# 分類は recommendation との対応づけに使う:
#   賃貸住宅 → rent / 宿泊・商業・オフィス・地域拠点 → 幅広く
USECASE_EXAMPLES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "栃木県",
        "あったか住まいるバンク（栃木市）",
        "賃貸住宅",
        "市の空き家バンクに登録し、改修・家財処分・解体それぞれに補助を受けながら貸し出す仕組み。",
        "リフォーム補助 最大50万円 / 家財処分 最大10万円 / 解体費用 最大50万円。683件登録・476件成約（成約率69.6%）",
    ),
    (
        "岐阜県",
        "飛騨市住むとこネット（飛騨市）",
        "賃貸住宅",
        "官民連携の空き家バンク。住居だけでなく店舗としての活用にも補助が出る。",
        "改修工事費 最大250万円 / 店舗リノベーション 最大100万円。299件成約（令和7年10月末時点）",
    ),
    (
        "沖縄県",
        "定住促進空家活用住宅（国頭村）",
        "賃貸住宅",
        "村が空き家を借り上げ、地域の担い手となる世帯に低廉な家賃で貸す。地域行事への参加が条件。",
        "月額家賃 33,000円（令和7年3月入居例）",
    ),
    (
        "沖縄県",
        "お試し移住住宅（うるま市）",
        "賃貸住宅",
        "離島の空き家を短期滞在型の住宅として整備し、移住検討者に貸す。古民家食堂の併設で雇用も生んだ。",
        "6年間で11組・25人の移住実績",
    ),
    (
        "和歌山県",
        "千山庵（湯浅町）",
        "宿泊施設",
        "築130〜150年の古民家を一棟貸しの宿に改修。伝統建築を保全しながら観光資源にした。",
        "築130〜150年の古民家を活用",
    ),
    (
        "長野県",
        "Satoyama Villa 本陣（松本市）",
        "宿泊施設",
        "江戸期の本陣跡（大正2年再建）を宿泊施設として再生。登録有形文化財の指定を受けた。",
        "令和3年 登録有形文化財指定 / 令和6年 観光庁長官賞受賞",
    ),
    (
        "徳島県",
        "神山プロジェクト（神山町）",
        "サテライトオフィス",
        "空き家をIT企業のサテライトオフィスと移住者向け住宅に転用し、過疎地に人の流れを作った。",
        "16社進出（令和7年6月時点）/ 平成24年に社会動態人口 +12人",
    ),
    (
        "山梨県",
        "まるごとサテライトオフィス構想（富士吉田市）",
        "サテライトオフィス",
        "市内の空き家・空きスペースを分散型オフィスとして束ね、まち全体を働く場所にした。",
        "約40拠点の提携施設",
    ),
    (
        "滋賀県",
        "MAIBARA EAST01（米原市）",
        "サテライトオフィス",
        "駅前の空き家を民間主導でシェアオフィスに改修。立地の良い空き家の再生モデル。",
        "駅前空き家を改修",
    ),
    (
        "宮城県",
        "子育てシェアスペース Omusubi（名取市）",
        "地域拠点",
        "空き家を託児所・コワーキング・シェアハウスの複合施設に。子育て支援と移住女性の定住を兼ねる。",
        "生後2ヶ月〜小学生が対象",
    ),
    (
        "沖縄県",
        "仲原家（久米島町）",
        "地域拠点",
        "琉球古民家をコワーキング・移住相談窓口・学習支援拠点として多目的に使う。",
        "多目的地域拠点",
    ),
    (
        "福岡県",
        "北九州未来づくりラボ（北九州市）",
        "地域拠点",
        "空き家をセーフティネット住宅として整備。低所得者・高齢者・子育て世帯・移住希望者が対象。",
        "地域共生型の住宅政策として展開",
    ),
    (
        "岡山県",
        "とくらす（瀬戸内市）",
        "商業施設",
        "築100年超の古民家を複数改修し、カフェ・デザイン工房・美容院・交流拠点として運営。",
        "築100年超の古民家を複数改修",
    ),
    (
        "沖縄県",
        "かめたろうやー（名護市）",
        "宿泊施設",
        "築120年の古民家を居酒屋兼カフェとして使い、その後民泊施設へ業態転換した。",
        "築120年の古民家を活用",
    ),
    (
        "広島県",
        "尾道空き家再生プロジェクト（尾道市）",
        "商業施設",
        "斜面地の空き家をゲストハウス・カフェ・コミュニティスペースに再生し続けている。",
        "20件以上を改修、100件以上の再生実績",
    ),
)


def seed_reference_data(conn: sqlite3.Connection) -> None:
    """出典のある参照データを投入する。再実行しても行が増えない。"""
    conn.executemany(
        """
        INSERT INTO prefecture_regions (prefecture, region)
        VALUES (?, ?)
        ON CONFLICT(prefecture) DO UPDATE SET region = excluded.region
        """,
        list(PREFECTURE_REGIONS.items()),
    )
    conn.executemany(
        """
        INSERT INTO demolition_costs (region, structure, cost_per_tsubo)
        VALUES (?, ?, ?)
        ON CONFLICT(region, structure) DO UPDATE SET
            cost_per_tsubo = excluded.cost_per_tsubo
        """,
        demolition_rows(),
    )
    conn.executemany(
        """
        INSERT INTO usecase_examples (
            prefecture, region, title, category, summary, numbers,
            source_name, source_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(title) DO UPDATE SET
            prefecture = excluded.prefecture,
            region     = excluded.region,
            category   = excluded.category,
            summary    = excluded.summary,
            numbers    = excluded.numbers
        """,
        [
            (
                prefecture,
                PREFECTURE_REGIONS.get(prefecture, "その他"),
                title,
                category,
                summary,
                numbers,
                USECASE_SOURCE_NAME,
                USECASE_SOURCE_URL,
            )
            for prefecture, title, category, summary, numbers in USECASE_EXAMPLES
        ],
    )

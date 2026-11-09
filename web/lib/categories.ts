/**
 * The 12-category signal taxonomy (design doc §4.1) with bilingual labels.
 * The V-prefixed categories are the proprietary pipelines; V2 and V4 are the
 * MVP ones. Unknown category strings in data still render — they fall back to
 * the raw value.
 */
export interface CategoryConfig {
  id: string;
  labelEn: string;
  labelZh: string;
  proprietary: boolean;
}

export const CATEGORIES: CategoryConfig[] = [
  { id: "G1_HIRING", labelEn: "Hiring", labelZh: "关键岗位招聘", proprietary: false },
  { id: "G2_PATENT", labelEn: "Patent", labelZh: "专利", proprietary: false },
  {
    id: "G3_NEW_CUSTOMER_PRODUCT",
    labelEn: "New Customer / Product",
    labelZh: "新客户与新产品",
    proprietary: false,
  },
  {
    id: "G4_GOVERNMENT_CONTRACT",
    labelEn: "Government Contract",
    labelZh: "政府合同",
    proprietary: false,
  },
  { id: "G5_CONFERENCE", labelEn: "Conference", labelZh: "会议表态", proprietary: false },
  {
    id: "G6_EARNINGS_SURPRISE",
    labelEn: "Earnings Surprise",
    labelZh: "业绩超预期",
    proprietary: false,
  },
  { id: "G7_INSIDER_13F", labelEn: "Insider & 13F", labelZh: "内部人与机构持仓", proprietary: false },
  { id: "G8_SUPPLY_CHAIN", labelEn: "Supply Chain", labelZh: "供应链", proprietary: false },
  {
    id: "V1_UTILITY_AWARD",
    labelEn: "Utility Award",
    labelZh: "公用事业订单",
    proprietary: true,
  },
  {
    id: "V2_INTERCONNECTION",
    labelEn: "Interconnection & Grid",
    labelZh: "并网排队与输电",
    proprietary: true,
  },
  {
    id: "V3_GOVERNMENT_FUNDING",
    labelEn: "Government Funding",
    labelZh: "政府资金",
    proprietary: true,
  },
  {
    id: "V4_DATACENTER_BUILDOUT",
    labelEn: "Data Center Buildout",
    labelZh: "数据中心建设",
    proprietary: true,
  },
];

const CATEGORY_BY_ID = new Map(CATEGORIES.map((c) => [c.id, c]));

export function categoryConfig(id: string): CategoryConfig | undefined {
  return CATEGORY_BY_ID.get(id);
}

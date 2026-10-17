// 星穹星座配色：深空蓝紫底 + 星云、星点、金色星座线、星系仪面板。只放颜色与参数，不含交互。
// 颜色一律用十六进制或 'r,g,b' 三元组字符串，方便 canvas 里直接拼 rgba()。

export const T = {
  // 星点颜色：按节点类型分色（作品星偏蓝紫、知识星偏青、选题星偏金、草稿星偏蓝、概念星纯白）
  kinds: {
    core: '#fff3d2',
    folder: '#c9b6ff',
    post: '#c3b0ff',
    note: '#a6e6d2',
    idea: '#ffd08a',
    draft: '#a3caff',
    concept: '#eaf0ff',
    tag: '#98a4c6',
    ghost: '#5f6c96'
  },
  // 五个星团：色温各有微差（蓝紫 / 青白 / 冷蓝 / 淡金 / 青绿白）
  modules: {
    works: {color: '#bda9ff', warm: '#e6ddff', cool: '#8f7bf0'},
    concepts: {color: '#e2eaff', warm: '#fff6e2', cool: '#9fb6e8'},
    methods: {color: '#a5dcff', warm: '#e2f4ff', cool: '#7aa8e8'},
    ideas: {color: '#ffd79a', warm: '#fff0cf', cool: '#e8b06a'},
    drafts: {color: '#9ff0e0', warm: '#dcfff5', cool: '#6cc8c8'}
  },
  // 星座线：全部金色系（去掉白/蓝/绿杂色）。near 是簇内亮线，far 是跨簇暗线，flow 是流动光粒
  line: {core: '#e8c884', glow: '#b98f3c', dim: '#7d6a44', flow: '#fff2cd'},
  link: {near: '#e2c88f', far: '#9b8354', flow: '#ffeec4'},
  // 星核
  core: {nucleus: '#fffdf4', corona: '#ffd98f', halo: '190,206,255', flare: '#fff0c4'},
  // 星核向外飘散的星尘（'r,g,b'）
  dust: ['236,240,255', '255,231,182', '186,206,255', '255,255,255'],
  pulse: {out: ['#fffaf0', '#ffd98f'], in: ['#fffdf4', '#e8c884']},
  // 背景宇宙：基底 / 星云三层 / 银河带 / 星尘 / 流星
  sky: {
    base: ['#070912', '#0b0f22', '#04050c'],
    nebula: [
      {colors: ['58,86,190', '104,72,196'], alpha: .58, scale: 1.0, seed: 11},
      {colors: ['38,120,180', '150,60,140'], alpha: .42, scale: 1.55, seed: 29},
      {colors: ['190,146,66', '212,86,120'], alpha: .38, scale: 2.3, seed: 47}
    ],
    milky: {tint: '168,188,255', warm: '255,232,186'},
    // 星点色温：以 A/F 型白为主，少量金黄（G/K）与橙红（M）点缀，不出现「彩色小圆点」
    star: ['242,246,255', '226,234,255', '255,250,238', '255,240,214', '255,226,186', '255,214,170'],
    meteor: {head: '255,253,246', tail: '196,214,255'}
  },
  // 内页小图谱（创作台右栏）
  mini: {glow0: '#fffdf4', core: '#9fc0ff', dot: '#ffeec2', mutual: '#e8c884', pulseOut: '#fff2cd', pulseIn: '#bda9ff'},
  // 首页背景星图（NetworkCanvas）：连线统一金色系，簇内亮、跨簇暗；节点仍按类型着色
  canvas: {
    additive: true, mutual: '238,206,138', mutualIn: '214,176,104', wiki: '226,198,138', link: '158,134,86',
    pulseOne: '236,206,148', label: '#e8ecf8', spark: ['232,208,150', '255,236,190', '255,255,255'], glow: '3a', light: '#ffffff'
  },
  ring: ['#ffd98f', '#bda9ff'],
  logo: ['#3b4a86', '#a9bcff', '#ffd98f', '#7f8fd0']
};

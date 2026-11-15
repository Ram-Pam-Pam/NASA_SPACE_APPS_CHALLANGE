// The data files use Polish field values; they are translated for display only.

export const CLASS_ORDER = ['wysokie', 'średnie', 'niskie', 'brak danych'];
export const CLASS_EN = {
  wysokie: 'High',
  średnie: 'Medium',
  niskie: 'Low',
  'brak danych': 'No data',
};

export const TYPE_EN = {
  gazociąg: 'Gas pipeline',
  rurociąg: 'Pipeline',
  ropociąg: 'Oil pipeline',
  kolej: 'Railway',
  'kolej nieczynna': 'Disused railway',
  'linia energetyczna': 'Power line',
  'droga główna': 'Major road',
  'droga lokalna': 'Local road',
  budynek: 'Building (untyped)',
};
export const typeEn = (t) => TYPE_EN[t] || (t ? t.replace(/_/g, ' ') : '');

export const CATEGORY_EN = { budynek: 'Building', infrastruktura: 'Infrastructure' };

export const ALERT_EN = {
  przyspieszenie: 'Acceleration',
  'nowa strefa ruchu': 'New moving area',
  spowolnienie: 'Deceleration',
};

export const CONFIDENCE_EN = {
  pełna: 'Full series',
  częściowa: 'Partial (≥ 70 % of pairs)',
};

export const STRUCTURE_EN = { most: 'Bridge', tunel: 'Tunnel', 'pod ziemią': 'Underground' };

export const BASELINE_EN = { 'brak (rozbieg)': 'none (spin-up)', trailing: 'trailing windows' };

export const PROFILE_EN = {
  os_dluga: 'Long axis of the landslide',
  os_krotka: 'Short axis of the landslide',
  Narcissa_Drive: 'Narcissa Drive',
  Palos_Verdes_Drive_South: 'Palos Verdes Drive South',
  Harbor_Subdivision: 'Harbor Subdivision (rail)',
};

// Reason strings: translate known phrases, keep numbers as they are in the data.
const REASON_RULES = [
  [/w granicach szumu/g, 'within measurement noise'],
  [/za mało wiarygodnych pikseli \(woda, utrata spójności\)/g, 'too few reliable pixels (water, loss of coherence)'],
  [/w aktywnej strefie ruchu/g, 'in the active zone'],
  [/w strefie ruchu/g, 'in a moving zone'],
  [/ruch zróżnicowany/g, 'differential motion'],
  [/przecina granicę strefy ruchu/g, 'crosses a moving-zone boundary'],
  [/alert: nowa strefa ruchu/g, 'alert: new moving area'],
  [/próg/g, 'threshold'],
  [/mm\/100m\/rok/g, 'mm/100 m/yr'],
  [/mm\/rok/g, 'mm/yr'],
];
export function reasonEn(s) {
  if (!s) return '';
  let out = s;
  for (const [re, rep] of REASON_RULES) out = out.replace(re, rep);
  return out;
}

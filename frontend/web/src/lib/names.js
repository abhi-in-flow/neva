// TV-safe generated nicknames (brief 01): playful, Indian flavor, no real
// names, nothing that embarrasses anyone on the venue screen. Curated
// adjective × noun pools give ~400 combos for the shuffle die.
const FIRST = [
  'Chai', 'Jalebi', 'Momo', 'Toofan', 'Bijli', 'Dhamaal',
  'Masala', 'Rocket', 'Mirchi', 'Dhol', 'Champa', 'Laddu',
  'Pataka', 'Samosa', 'Monsoon', 'Kesar', 'Tez', 'Jugaad',
  'Bindaas', 'Nimbu',
];
const SECOND = [
  'Champion', 'Express', 'Master', 'Rani', 'Raja', 'Rockstar',
  'Captain', 'Genius', 'Tiger', 'Comet', 'Dynamo', 'Legend',
  'Sultan', 'Star', 'Boss', 'Ace', 'Storm', 'Wizard',
  'Panther', 'Falcon',
];

export function generateName(previous) {
  let name = previous;
  while (name === previous) {
    const f = FIRST[Math.floor(Math.random() * FIRST.length)];
    const s = SECOND[Math.floor(Math.random() * SECOND.length)];
    name = `${f} ${s}`;
  }
  return name;
}

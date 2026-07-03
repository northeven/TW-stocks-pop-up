// 房間管理：每個房間有人數上限，滿了就自動開新房。
// 彈幕只在房間內廣播，控制每則訊息的扇出（fan-out）成本。

export class RoomManager {
  constructor({ capacity = 100 } = {}) {
    this.capacity = capacity;
    this.counts = new Map(); // roomName -> 目前人數
  }

  roomName(i) {
    return `room-${i}`;
  }

  /** 找出第一個還有空位的房間並入座，回傳房名。 */
  assign() {
    for (let i = 1; ; i++) {
      const name = this.roomName(i);
      const count = this.counts.get(name) ?? 0;
      if (count < this.capacity) {
        this.counts.set(name, count + 1);
        return name;
      }
    }
  }

  /** 離開房間；房間清空就移除，讓編號可以重複利用。 */
  leave(name) {
    const count = this.counts.get(name);
    if (count === undefined) return;
    if (count <= 1) this.counts.delete(name);
    else this.counts.set(name, count - 1);
  }

  count(name) {
    return this.counts.get(name) ?? 0;
  }

  totalUsers() {
    let total = 0;
    for (const c of this.counts.values()) total += c;
    return total;
  }

  totalRooms() {
    return this.counts.size;
  }
}

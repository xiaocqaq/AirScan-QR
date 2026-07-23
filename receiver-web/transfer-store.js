(function (root, factory) {
  'use strict';
  const api = factory(root);
  root.AirScan = root.AirScan || {};
  root.AirScan.store = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  const DB_NAME = 'airscan_receiver';
  const DB_VERSION = 1;

  function tidKey(tid) {
    if (typeof tid === 'string') return tid;
    return Array.from(tid, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.addEventListener('success', () => resolve(request.result), { once: true });
      request.addEventListener('error', () => reject(request.error), { once: true });
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.addEventListener('complete', resolve, { once: true });
      transaction.addEventListener('abort', () => reject(transaction.error), { once: true });
      transaction.addEventListener('error', () => reject(transaction.error), { once: true });
    });
  }

  function upgradeDatabase(database) {
    if (!database.objectStoreNames.contains('tasks')) {
      const tasks = database.createObjectStore('tasks', { keyPath: 'tid' });
      tasks.createIndex('byStatus', 'status', { unique: false });
    }
    if (!database.objectStoreNames.contains('chunks')) {
      const chunks = database.createObjectStore('chunks', { keyPath: ['tid', 'index'] });
      chunks.createIndex('byTid', 'tid', { unique: false });
    }
    if (!database.objectStoreNames.contains('received')) {
      const received = database.createObjectStore('received', { keyPath: ['tid', 'index'] });
      received.createIndex('byTid', 'tid', { unique: false });
    }
  }

  async function openStore() {
    if (!root.indexedDB) throw new Error('当前浏览器不支持 IndexedDB 本地存储');
    const request = root.indexedDB.open(DB_NAME, DB_VERSION);
    request.addEventListener('upgradeneeded', () => upgradeDatabase(request.result));
    const database = await requestResult(request);
    return new IndexedDbStore(database);
  }

  function taskRecord(meta, key) {
    return {
      tid: key,
      flags: meta.flags,
      total: meta.total,
      chunkSize: meta.chunkSize,
      fileSize: meta.fileSize,
      name: meta.name,
      sha1: Array.from(meta.sha1),
      receivedCount: 0,
      status: 'receiving',
      lastUpdated: Date.now(),
    };
  }

  class IndexedDbStore {
    constructor(database) {
      this.database = database;
    }

    async upsertTask(meta) {
      const key = tidKey(meta.tid);
      const transaction = this.database.transaction('tasks', 'readwrite');
      const store = transaction.objectStore('tasks');
      const existing = await requestResult(store.get(key));
      if (existing) return existing;
      const record = taskRecord(meta, key);
      store.add(record);
      await transactionDone(transaction);
      return record;
    }

    async getTask(tid) {
      const transaction = this.database.transaction('tasks', 'readonly');
      return requestResult(transaction.objectStore('tasks').get(tidKey(tid)));
    }

    async putChunk(tid, index, payload) {
      const key = tidKey(tid);
      const transaction = this.database.transaction(['tasks', 'chunks', 'received'], 'readwrite');
      const done = transactionDone(transaction);
      const chunks = transaction.objectStore('chunks');
      // 跳过 exists get：调用方(ReceiverCore)已用内存 Set 去重
      const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
      const data = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
      chunks.put({ tid: key, index, payload: data });
      transaction.objectStore('received').put({ tid: key, index });
      const tasks = transaction.objectStore('tasks');
      const task = await requestResult(tasks.get(key));
      if (task) {
        task.receivedCount = (task.receivedCount || 0) + 1;
        task.lastUpdated = Date.now();
        tasks.put(task);
      }
      await done;
      return true;
    }

    async hasChunk(tid, index) {
      const transaction = this.database.transaction('chunks', 'readonly');
      const record = await requestResult(transaction.objectStore('chunks').get([tidKey(tid), index]));
      return Boolean(record);
    }

    async readChunks(tid, total) {
      const key = tidKey(tid);
      const transaction = this.database.transaction('chunks', 'readonly');
      const store = transaction.objectStore('chunks');
      const requests = Array.from({ length: total }, (_, index) => requestResult(store.get([key, index])));
      const records = await Promise.all(requests);
      if (records.some((record) => !record)) throw new Error('文件分片不完整');
      return records.map((record) => new Uint8Array(record.payload));
    }

    async getReceivedIndices(tid) {
      const transaction = this.database.transaction('received', 'readonly');
      const index = transaction.objectStore('received').index('byTid');
      const records = await requestResult(index.getAll(root.IDBKeyRange.only(tidKey(tid))));
      return records.map((record) => record.index).sort((left, right) => left - right);
    }

    async listIncompleteTasks() {
      const transaction = this.database.transaction('tasks', 'readonly');
      const tasks = await requestResult(transaction.objectStore('tasks').getAll());
      return tasks.filter((task) => task.status !== 'complete')
        .sort((left, right) => right.lastUpdated - left.lastUpdated);
    }

    async setTaskStatus(tid, status) {
      const transaction = this.database.transaction('tasks', 'readwrite');
      const store = transaction.objectStore('tasks');
      const task = await requestResult(store.get(tidKey(tid)));
      if (!task) return;
      task.status = status;
      task.lastUpdated = Date.now();
      store.put(task);
      await transactionDone(transaction);
    }

    async markDone(tid) {
      const transaction = this.database.transaction('tasks', 'readwrite');
      const store = transaction.objectStore('tasks');
      const task = await requestResult(store.get(tidKey(tid)));
      if (!task) return;
      task.status = 'complete';
      task.done = true;
      task.lastUpdated = Date.now();
      store.put(task);
      await transactionDone(transaction);
    }

    async resetTask(tid) {
      const key = tidKey(tid);
      await Promise.all([this._deleteByTid('chunks', key), this._deleteByTid('received', key)]);
      const transaction = this.database.transaction('tasks', 'readwrite');
      const store = transaction.objectStore('tasks');
      const task = await requestResult(store.get(key));
      task.receivedCount = 0;
      task.status = 'receiving';
      task.lastUpdated = Date.now();
      store.put(task);
      await transactionDone(transaction);
    }

    async _deleteByTid(storeName, tid) {
      const transaction = this.database.transaction(storeName, 'readwrite');
      const store = transaction.objectStore(storeName);
      const request = store.index('byTid').openKeyCursor(root.IDBKeyRange.only(tid));
      request.addEventListener('success', () => {
        const cursor = request.result;
        if (!cursor) return;
        store.delete(cursor.primaryKey);
        cursor.continue();
      });
      await transactionDone(transaction);
    }

    async deleteTask(tid) {
      const key = tidKey(tid);
      await Promise.all([this._deleteByTid('chunks', key), this._deleteByTid('received', key)]);
      const transaction = this.database.transaction('tasks', 'readwrite');
      transaction.objectStore('tasks').delete(key);
      await transactionDone(transaction);
    }
  }

  return { DB_NAME, IndexedDbStore, openStore, tidKey };
}));

-- ============================================
-- 仓库管理系统 - 数据库初始化脚本
-- 适用于 MySQL 5.7+ / 8.0
-- ============================================

CREATE DATABASE IF NOT EXISTS warehouse_db
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE warehouse_db;

-- --------------------------------------------
-- 1. 商品分类表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(100)    NOT NULL UNIQUE,
    description     TEXT,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- --------------------------------------------
-- 2. 商品表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200)    NOT NULL,
    sku             VARCHAR(100)    NOT NULL UNIQUE,
    category_id     INT,
    unit            VARCHAR(50)     NOT NULL DEFAULT '个',
    specification   VARCHAR(200),
    description     TEXT,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL,
    INDEX idx_sku (sku),
    INDEX idx_category (category_id),
    INDEX idx_name (name)
) ENGINE=InnoDB;

-- --------------------------------------------
-- 3. 库存表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS inventory (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    product_id      INT             NOT NULL UNIQUE,
    quantity        INT             NOT NULL DEFAULT 0,
    location        VARCHAR(100)    DEFAULT '',
    min_stock       INT             NOT NULL DEFAULT 0,
    max_stock       INT             NOT NULL DEFAULT 9999,
    last_updated    TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_quantity (quantity),
    INDEX idx_location (location)
) ENGINE=InnoDB;

-- --------------------------------------------
-- 4. 出入库记录表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    product_id      INT             NOT NULL,
    type            ENUM('in', 'out') NOT NULL,
    quantity        INT             NOT NULL,
    before_qty      INT             NOT NULL DEFAULT 0,
    after_qty       INT             NOT NULL DEFAULT 0,
    batch_no        VARCHAR(100)    DEFAULT '',
    operator        VARCHAR(100)    DEFAULT '',
    notes           TEXT,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_product (product_id),
    INDEX idx_type (type),
    INDEX idx_created (created_at)
) ENGINE=InnoDB;

-- --------------------------------------------
-- 5. Excel 上传记录表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS excel_uploads (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    filename        VARCHAR(255)    NOT NULL,
    file_size       BIGINT          DEFAULT 0,
    rows_processed  INT             DEFAULT 0,
    status          ENUM('pending', 'processing', 'success', 'failed') DEFAULT 'pending',
    error_message   TEXT,
    uploaded_at     TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- --------------------------------------------
-- 5. 供应商表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200)    NOT NULL,
    contact_person  VARCHAR(100)    DEFAULT '',
    phone           VARCHAR(50)     DEFAULT '',
    email           VARCHAR(100)    DEFAULT '',
    address         VARCHAR(300)    DEFAULT '',
    notes           TEXT,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_name (name)
) ENGINE=InnoDB;

-- --------------------------------------------
-- 6. 客户表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(200)    NOT NULL,
    contact_person  VARCHAR(100)    DEFAULT '',
    phone           VARCHAR(50)     DEFAULT '',
    email           VARCHAR(100)    DEFAULT '',
    address         VARCHAR(300)    DEFAULT '',
    notes           TEXT,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_name (name)
) ENGINE=InnoDB;

-- 为已有表增加供应商/客户字段（安全执行，已存在则跳过）
-- products 增加 supplier_id
SET @col_exists = 0;
SELECT COUNT(*) INTO @col_exists
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' AND COLUMN_NAME = 'supplier_id';
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE products ADD COLUMN supplier_id INT DEFAULT NULL AFTER category_id, ADD FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL',
    'SELECT "supplier_id already exists"');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- transactions 增加 customer_id
SET @col_exists = 0;
SELECT COUNT(*) INTO @col_exists
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'transactions' AND COLUMN_NAME = 'customer_id';
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE transactions ADD COLUMN customer_id INT DEFAULT NULL AFTER product_id, ADD FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL',
    'SELECT "customer_id already exists"');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- products 增加 unit_price(进货单价) 和 sale_price(售价)
SET @col_exists = 0;
SELECT COUNT(*) INTO @col_exists
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' AND COLUMN_NAME = 'unit_price';
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE products ADD COLUMN unit_price DECIMAL(12,2) DEFAULT 0 AFTER specification',
    'SELECT "unit_price already exists"');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_exists = 0;
SELECT COUNT(*) INTO @col_exists
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' AND COLUMN_NAME = 'sale_price';
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE products ADD COLUMN sale_price DECIMAL(12,2) DEFAULT 0 AFTER unit_price',
    'SELECT "sale_price already exists"');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- --------------------------------------------
-- 7. 操作审计日志表
-- --------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    action          VARCHAR(20)     NOT NULL,
    table_name      VARCHAR(50)     NOT NULL,
    record_id       INT             DEFAULT NULL,
    old_data        TEXT,
    new_data        TEXT,
    operator        VARCHAR(100)    DEFAULT 'system',
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_table (table_name),
    INDEX idx_action (action),
    INDEX idx_created (created_at)
) ENGINE=InnoDB;

-- --------------------------------------------
-- 插入一些示例数据
-- --------------------------------------------
INSERT INTO categories (name, description) VALUES
    ('电子产品', '电子元器件及成品'),
    ('机械零件', '机械设备零配件'),
    ('原材料', '生产原材料'),
    ('包装材料', '包装及辅料'),
    ('办公用品', '办公消耗品')
ON DUPLICATE KEY UPDATE name=name;

INSERT INTO products (name, sku, category_id, unit, specification) VALUES
    ('螺丝 M6x20', 'SCR-M6-020', 2, '盒', '不锈钢 六角 M6x20mm'),
    ('电阻 10KΩ', 'RES-10K', 1, '个', '0805 贴片 ±5%'),
    ('打印纸 A4', 'PAP-A4', 5, '包', '500张/包 80g'),
    ('密封圈 Φ50', 'SEAL-050', 2, '个', '丁腈橡胶 NBR'),
    ('电容 100μF', 'CAP-100U', 1, '个', '铝电解 25V')
ON DUPLICATE KEY UPDATE sku=sku;

INSERT INTO inventory (product_id, quantity, location, min_stock, max_stock) VALUES
    (1, 1500, 'A-01-03', 200, 5000),
    (2, 300, 'B-02-01', 500, 10000),
    (3, 80, 'C-01-05', 50, 500),
    (4, 45, 'A-02-02', 100, 2000),
    (5, 1200, 'B-01-04', 300, 5000)
ON DUPLICATE KEY UPDATE product_id=product_id;

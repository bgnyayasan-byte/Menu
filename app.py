import streamlit as st
import zipfile
import io
import os
import re
import tempfile
import pandas as pd
import pdfplumber
from datetime import datetime, timedelta

st.set_page_config(page_title="PDF Stok Processor Enhanced", layout="wide")

# -------------------------
# CONFIG / DEFAULTS
# -------------------------
LOCATIONS = ["Llagang", "Batoh", "Merduati", "Ldingin", "Cadek", "Pkn Bil", "Seutui"]

# Default NEEDS_PER_PORTION with gramasi (weight/unit per portion)
DEFAULT_NEEDS = {
    "Asam Jawa": {"small": 0.02, "large": 0.04, "unit": "kg"},
    "Bawang Merah": {"small": 0.05, "large": 0.1, "unit": "kg"},
    "Bawang Putih": {"small": 0.03, "large": 0.06, "unit": "kg"},
    "Beras": {"small": 0.1, "large": 0.2, "unit": "kg"},
    "Bumbu Giling Putih": {"small": 0.01, "large": 0.02, "unit": "kg"},
    "Cabe Giling Merah": {"small": 0.02, "large": 0.04, "unit": "kg"},
    "Bumbu Giling Merah": {"small": 0.015, "large": 0.03, "unit": "kg"},
    "Daun Pra": {"small": 0.005, "large": 0.01, "unit": "kg"},
    "Daun Salam": {"small": 0.002, "large": 0.004, "unit": "kg"},
    "Garam": {"small": 0.01, "large": 0.02, "unit": "kg"},
    "Gula Merah": {"small": 0.015, "large": 0.03, "unit": "kg"},
    "Gula Pasir": {"small": 0.01, "large": 0.02, "unit": "kg"},
    "Kacang Panjang": {"small": 0.05, "large": 0.1, "unit": "kg"},
    "Kentang": {"small": 0.08, "large": 0.15, "unit": "kg"},
    "Minyak": {"small": 0.02, "large": 0.04, "unit": "kg"},
    "Royco": {"small": 0.005, "large": 0.01, "unit": "kg"},
    "Tahu": {"small": 0.05, "large": 0.1, "unit": "kg"},
    "Tempe": {"small": 0.05, "large": 0.1, "unit": "kg"},
    "Teri": {"small": 0.02, "large": 0.04, "unit": "kg"},
    "Wortel": {"small": 0.05, "large": 0.1, "unit": "kg"},
}

# Regex helpers
ANGKA_REGEX = re.compile(r"(\d+(?:[\.,]\d+)?)")
FILE_DATE_REGEX = re.compile(r"(\d{1,2})[_\s-](\d{1,2})[_\s-](\d{2,4})")

# -------------------------
# SESSION STATE INIT
# -------------------------
if "penarikan_data" not in st.session_state:
    st.session_state.penarikan_data = {}

if "porsi_data" not in st.session_state:
    st.session_state.porsi_data = {loc: {"small": 0, "large": 0} for loc in LOCATIONS}

if "needs_config" not in st.session_state:
    st.session_state.needs_config = DEFAULT_NEEDS.copy()

# -------------------------
# UTIL FUNCTIONS
# -------------------------
def parse_date_from_filename(fname):
    m = FILE_DATE_REGEX.search(fname)
    if not m:
        return None
    day, month, year = m.groups()
    day = int(day)
    month = int(month)
    year = int(year)
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day).date()
    except:
        return None

def extract_number(cell):
    if cell is None:
        return None
    s = str(cell).replace(",", ".")
    m = ANGKA_REGEX.search(s)
    if not m:
        return None
    val = float(m.group(1))
    if val.is_integer():
        return int(val)
    return val

def extract_tables_from_pdf_path(pdf_path):
    rows = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for tbl in page.extract_tables() or []:
                    rows.extend(tbl)
    except Exception as e:
        st.error(f"Error reading {pdf_path}: {e}")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.dropna(how="all")
    return df

def normalize_raw_table(df_raw, source_filename):
    if df_raw.empty:
        return pd.DataFrame()

    header_idx = None
    for i, row in df_raw.iterrows():
        joined = " ".join([str(x) for x in row.values if x is not None])
        if "NAMA BARANG" in joined.upper():
            header_idx = i
            break
    if header_idx is None:
        header_idx = 0

    df = df_raw.iloc[header_idx+1:].reset_index(drop=True).copy()
    num_cols = df.shape[1]
    colnames = []
    for i in range(num_cols):
        if i == 0:
            colnames.append("NO")
        elif i == 1:
            colnames.append("NAMA BARANG")
        else:
            colnames.append(f"COL_{i}")
    df.columns = colnames

    dt = parse_date_from_filename(source_filename)
    df["Tanggal"] = dt
    df["Sumber File"] = source_filename

    header_row_values = df_raw.iloc[header_idx] if header_idx < len(df_raw) else None
    header_text = ""
    if header_row_values is not None:
        header_text = " ".join([str(x) for x in header_row_values if x is not None]).lower()

    location_cols = {}
    for loc in LOCATIONS:
        if loc.lower() in header_text:
            for j, val in enumerate(header_row_values):
                if val and loc.lower() in str(val).lower():
                    colname = colnames[j]
                    location_cols[loc] = colname
                    break

    unmapped_locs = [l for l in LOCATIONS if l not in location_cols]
    candidate_cols = [c for c in colnames if c.startswith("COL_")]
    for idx, loc in enumerate(unmapped_locs):
        if idx < len(candidate_cols):
            location_cols[loc] = candidate_cols[idx]

    tidy_rows = []
    for _, row in df.iterrows():
        item = str(row.get("NAMA BARANG", "")).strip()
        if item == "" or item.lower() in ["", "nan", "no", "nama barang"]:
            continue
        entry = {
            "NAMA BARANG": item,
            "Tanggal": row["Tanggal"],
            "Sumber File": row["Sumber File"]
        }
        total = 0
        any_val = False
        for loc, col in location_cols.items():
            val = extract_number(row.get(col))
            entry[loc] = val if val is not None else 0
            if val:
                any_val = True
                try:
                    total += float(val)
                except:
                    pass
        entry["Total"] = total if any_val else None
        tidy_rows.append(entry)

    tidy_df = pd.DataFrame(tidy_rows)
    cols_order = ["NAMA BARANG", "Tanggal"] + LOCATIONS + ["Total", "Sumber File"]
    for c in cols_order:
        if c not in tidy_df.columns:
            tidy_df[c] = None
    tidy_df = tidy_df[cols_order]
    return tidy_df

def process_uploaded_files(file_bytes_list, filenames):
    tmpd = tempfile.mkdtemp()
    pdf_paths = []
    for b, fname in zip(file_bytes_list, filenames):
        lower = fname.lower()
        target = os.path.join(tmpd, fname)
        with open(target, "wb") as f:
            f.write(b)
        if lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(target, "r") as z:
                    z.extractall(tmpd)
                    for n in z.namelist():
                        if n.lower().endswith(".pdf"):
                            pdf_paths.append(os.path.join(tmpd, n))
            except Exception as e:
                st.warning(f"Gagal mengekstrak zip {fname}: {e}")
        elif lower.endswith(".pdf"):
            pdf_paths.append(target)

    combined = []
    for p in sorted(pdf_paths):
        raw = extract_tables_from_pdf_path(p)
        tidy = normalize_raw_table(raw, os.path.basename(p))
        if not tidy.empty:
            combined.append(tidy)

    if combined:
        df_all = pd.concat(combined, ignore_index=True)
    else:
        df_all = pd.DataFrame(columns=["NAMA BARANG", "Tanggal"] + LOCATIONS + ["Total", "Sumber File"])
    df_all["Tanggal"] = pd.to_datetime(df_all["Tanggal"])
    return df_all

def rekap_per_day(df, date):
    df_f = df[df["Tanggal"].dt.date == date]
    if df_f.empty:
        return pd.DataFrame()
    agg = df_f.groupby("NAMA BARANG")[LOCATIONS].sum(min_count=1).fillna(0)
    agg["Total"] = agg.sum(axis=1)
    agg = agg.reset_index().sort_values("NAMA BARANG")
    return agg

def rekap_per_period(df, start_date, end_date):
    mask = (df["Tanggal"].dt.date >= start_date) & (df["Tanggal"].dt.date <= end_date)
    df_f = df[mask]
    if df_f.empty:
        return pd.DataFrame()
    agg = df_f.groupby("NAMA BARANG")[LOCATIONS].sum(min_count=1).fillna(0)
    agg["Total"] = agg.sum(axis=1)
    agg = agg.reset_index().sort_values("NAMA BARANG")
    return agg

def rekap_per_week(df):
    if df.empty:
        return {}
    min_date = df["Tanggal"].min().date()
    df2 = df.copy()
    df2["week_index"] = df2["Tanggal"].dt.date.apply(lambda d: ((d - min_date).days // 7) + 1)
    weeks = {}
    for w, group in df2.groupby("week_index"):
        start = group["Tanggal"].dt.date.min()
        end = group["Tanggal"].dt.date.max()
        agg = group.groupby("NAMA BARANG")[LOCATIONS].sum(min_count=1).fillna(0)
        agg["Total"] = agg.sum(axis=1)
        weeks[f"Minggu {w} ({start} - {end})"] = agg.reset_index().sort_values("NAMA BARANG")
    return weeks

# -------------------------
# UI
# -------------------------
st.title("📊 PDF Stok Processor — Enhanced dengan Gramasi & Penarikan Barang")
st.markdown("Upload file PDF/ZIP → Rekap Stok → Input Porsi → Hitung Kebutuhan → Penarikan Barang")

# File upload section
uploaded = st.file_uploader("Upload PDF atau ZIP", type=["pdf","zip"], accept_multiple_files=True)
file_bytes = []
filenames = []
if uploaded:
    for u in uploaded:
        file_bytes.append(u.read())
        filenames.append(u.name)

if st.button("🔄 Proses File"):
    with st.spinner("Memproses file..."):
        df_all = process_uploaded_files(file_bytes, filenames)
        st.session_state.df_all = df_all
        st.success(f"✅ Data berhasil diproses — {len(df_all)} baris")

if "df_all" not in st.session_state:
    st.info("👆 Upload file dan klik 'Proses File' untuk memulai")
    st.stop()

df_all = st.session_state.df_all
available_dates = sorted(df_all["Tanggal"].dt.date.unique())

# -------------------------
# MAIN FEATURE TABS
# -------------------------
tab1, tab2, tab3, tab4 = st.tabs(["📋 Rekap Stok", "⚙️ Kelola Menu & Gramasi", "🍽️ Input Porsi & Kebutuhan", "📦 Penarikan Barang"])

# TAB 1: REKAP STOK
with tab1:
    st.header("Rekap Stok")
    mode = st.radio("Mode rekap:", ("Per Hari", "Per Minggu", "Per Periode", "Total Semua"), horizontal=True)
    
    if mode == "Per Hari":
        date_choice = st.selectbox("Pilih tanggal:", available_dates)
        res = rekap_per_day(df_all, date_choice)
        st.dataframe(res, use_container_width=True)
        
    elif mode == "Per Minggu":
        weeks = rekap_per_week(df_all)
        if weeks:
            selected = st.selectbox("Pilih minggu:", list(weeks.keys()))
            st.dataframe(weeks[selected], use_container_width=True)
            
    elif mode == "Per Periode":
        col1, col2 = st.columns(2)
        with col1:
            start_d = st.date_input("Tanggal awal:", min(available_dates))
        with col2:
            end_d = st.date_input("Tanggal akhir:", max(available_dates))
        if start_d <= end_d:
            res = rekap_per_period(df_all, start_d, end_d)
            st.dataframe(res, use_container_width=True)
            
    else:  # Total Semua
        agg = df_all.groupby("NAMA BARANG")[LOCATIONS].sum(min_count=1).fillna(0)
        agg["Total"] = agg.sum(axis=1)
        agg = agg.reset_index().sort_values("NAMA BARANG")
        st.dataframe(agg, use_container_width=True)

# TAB 2: KELOLA MENU & GRAMASI
with tab2:
    st.header("⚙️ Kelola Menu & Gramasi Bahan")
    st.markdown("**Edit gramasi atau tambah menu baru untuk perhitungan kebutuhan**")
    
    # Convert needs config to editable DataFrame
    config_rows = []
    for item, values in st.session_state.needs_config.items():
        config_rows.append({
            "Nama Menu": item,
            "Gramasi Porsi Kecil": values.get("small", 0),
            "Gramasi Porsi Besar": values.get("large", 0),
            "Unit": values.get("unit", "kg")
        })
    
    config_df = pd.DataFrame(config_rows)
    
    st.subheader("📝 Edit Gramasi Menu yang Ada")
    edited_config = st.data_editor(
        config_df,
        column_config={
            "Nama Menu": st.column_config.TextColumn("Nama Menu", disabled=True),
            "Gramasi Porsi Kecil": st.column_config.NumberColumn(
                "Gramasi Porsi Kecil",
                min_value=0,
                step=0.001,
                format="%.3f"
            ),
            "Gramasi Porsi Besar": st.column_config.NumberColumn(
                "Gramasi Porsi Besar",
                min_value=0,
                step=0.001,
                format="%.3f"
            ),
            "Unit": st.column_config.SelectboxColumn(
                "Unit",
                options=["kg", "gram", "liter", "ml", "pcs", "butir"]
            )
        },
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic"
    )
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("💾 Simpan Perubahan"):
            # Update session state with edited values
            new_config = {}
            for _, row in edited_config.iterrows():
                new_config[row["Nama Menu"]] = {
                    "small": float(row["Gramasi Porsi Kecil"]),
                    "large": float(row["Gramasi Porsi Besar"]),
                    "unit": row["Unit"]
                }
            st.session_state.needs_config = new_config
            st.success("✅ Perubahan berhasil disimpan!")
            st.rerun()
    
    with col2:
        if st.button("🔄 Reset ke Default"):
            st.session_state.needs_config = DEFAULT_NEEDS.copy()
            st.success("✅ Reset ke konfigurasi default!")
            st.rerun()
    
    st.markdown("---")
    
    # Add new menu
    st.subheader("➕ Tambah Menu Baru")
    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
    
    with col1:
        new_menu_name = st.text_input("Nama Menu Baru", key="new_menu")
    with col2:
        new_small = st.number_input("Gramasi Kecil", min_value=0.0, step=0.01, key="new_small")
    with col3:
        new_large = st.number_input("Gramasi Besar", min_value=0.0, step=0.01, key="new_large")
    with col4:
        new_unit = st.selectbox("Unit", ["kg", "gram", "liter", "ml", "pcs", "butir"], key="new_unit")
    with col5:
        st.write("")  # spacing
        st.write("")  # spacing
        if st.button("➕ Tambah"):
            if new_menu_name.strip():
                if new_menu_name in st.session_state.needs_config:
                    st.warning(f"⚠️ Menu '{new_menu_name}' sudah ada!")
                else:
                    st.session_state.needs_config[new_menu_name] = {
                        "small": new_small,
                        "large": new_large,
                        "unit": new_unit
                    }
                    st.success(f"✅ Menu '{new_menu_name}' berhasil ditambahkan!")
                    st.rerun()
            else:
                st.warning("⚠️ Nama menu tidak boleh kosong!")
    
    # Option to delete menu
    st.markdown("---")
    st.subheader("🗑️ Hapus Menu")
    menu_to_delete = st.selectbox(
        "Pilih menu yang akan dihapus:",
        options=["-- Pilih Menu --"] + list(st.session_state.needs_config.keys())
    )
    
    if menu_to_delete != "-- Pilih Menu --":
        if st.button(f"🗑️ Hapus '{menu_to_delete}'"):
            del st.session_state.needs_config[menu_to_delete]
            st.success(f"✅ Menu '{menu_to_delete}' berhasil dihapus!")
            st.rerun()

# TAB 3: INPUT PORSI & KEBUTUHAN
with tab3:
    st.header("Input Porsi & Hitung Kebutuhan Bahan")
    
    selected_loc = st.selectbox("Pilih Lokasi:", LOCATIONS)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        porsi_kecil = st.number_input("Porsi Kecil", min_value=0, value=st.session_state.porsi_data[selected_loc]["small"], key=f"pk_{selected_loc}")
    with col2:
        porsi_besar = st.number_input("Porsi Besar", min_value=0, value=st.session_state.porsi_data[selected_loc]["large"], key=f"pb_{selected_loc}")
    with col3:
        if st.button("💾 Simpan Porsi"):
            st.session_state.porsi_data[selected_loc]["small"] = porsi_kecil
            st.session_state.porsi_data[selected_loc]["large"] = porsi_besar
            st.success("Porsi tersimpan!")
    
    # Calculate needs
    st.subheader(f"Kebutuhan Bahan - {selected_loc}")
    needs_rows = []
    for item, mapping in st.session_state.needs_config.items():
        gramasi_small = mapping.get("small", 0)
        gramasi_large = mapping.get("large", 0)
        unit = mapping.get("unit", "kg")
        
        need_small = gramasi_small * porsi_kecil
        need_large = gramasi_large * porsi_besar
        total_need = need_small + need_large
        
        needs_rows.append({
            "No": len(needs_rows) + 1,
            "Nama Barang": item,
            "Gramasi": f"{gramasi_small}/{gramasi_large}",
            "Porsi Kecil": porsi_kecil,
            "Porsi Besar": porsi_besar,
            "Total Kuantiti Barang": total_need,
            "Unit": unit,
            "Penarikan Porsi": "",
            "Pasarikan": 0
        })
    
    needs_df = pd.DataFrame(needs_rows)
    
    # Display as editable table
    st.dataframe(needs_df, use_container_width=True)
    
    # Download Excel
    out_buf = io.BytesIO()
    with pd.ExcelWriter(out_buf, engine="xlsxwriter") as writer:
        needs_df.to_excel(writer, sheet_name="Kebutuhan", index=False)
        
        # Add current stock data
        agg_stock = df_all.groupby("NAMA BARANG")[selected_loc].sum(min_count=1).reset_index()
        agg_stock.columns = ["Nama Barang", "Stok Tersedia"]
        agg_stock.to_excel(writer, sheet_name="Stok_Saat_Ini", index=False)
    out_buf.seek(0)
    
    st.download_button(
        f"📥 Download Kebutuhan {selected_loc}",
        data=out_buf,
        file_name=f"kebutuhan_{selected_loc}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    )

# TAB 4: PENARIKAN BARANG
with tab4:
    st.header("Penarikan Barang & Pengurangan Porsi")
    
    loc_tarik = st.selectbox("Pilih Lokasi untuk Penarikan:", LOCATIONS, key="loc_tarik")
    
    # Get current stock
    current_stock = df_all.groupby("NAMA BARANG")[loc_tarik].sum(min_count=1).to_dict()
    
    # Get current needs
    porsi_s = st.session_state.porsi_data[loc_tarik]["small"]
    porsi_l = st.session_state.porsi_data[loc_tarik]["large"]
    
    st.subheader("Tabel Penarikan Barang")
    
    penarikan_rows = []
    for item, mapping in st.session_state.needs_config.items():
        stock = current_stock.get(item, 0)
        need = (mapping.get("small", 0) * porsi_s) + (mapping.get("large", 0) * porsi_l)
        
        # Get previous withdrawal
        prev_withdraw = st.session_state.penarikan_data.get(loc_tarik, {}).get(item, 0)
        
        remaining = stock - prev_withdraw
        
        penarikan_rows.append({
            "No": len(penarikan_rows) + 1,
            "Nama Barang": item,
            "Stok Tersedia": stock,
            "Kebutuhan": need,
            "Penarikan Sebelumnya": prev_withdraw,
            "Sisa Stok": remaining,
            "Penarikan Baru": 0,
            "Status": "✅ Cukup" if remaining >= need else "⚠️ Kurang"
        })
    
    penarikan_df = pd.DataFrame(penarikan_rows)
    
    # Editable table for new withdrawals
    st.markdown("**Input penarikan baru di kolom 'Penarikan Baru':**")
    
    edited_df = st.data_editor(
        penarikan_df,
        column_config={
            "Penarikan Baru": st.column_config.NumberColumn(
                "Penarikan Baru",
                min_value=0,
                step=0.1,
                format="%.2f"
            )
        },
        hide_index=True,
        use_container_width=True
    )
    
    if st.button("💾 Simpan Penarikan"):
        if loc_tarik not in st.session_state.penarikan_data:
            st.session_state.penarikan_data[loc_tarik] = {}
        
        for _, row in edited_df.iterrows():
            item = row["Nama Barang"]
            new_withdraw = row["Penarikan Baru"]
            if new_withdraw > 0:
                current = st.session_state.penarikan_data[loc_tarik].get(item, 0)
                st.session_state.penarikan_data[loc_tarik][item] = current + new_withdraw
        
        st.success("✅ Penarikan berhasil disimpan!")
        st.rerun()
    
    # Download penarikan report
    out_buf2 = io.BytesIO()
    with pd.ExcelWriter(out_buf2, engine="xlsxwriter") as writer:
        edited_df.to_excel(writer, sheet_name="Penarikan", index=False)
    out_buf2.seek(0)
    
    st.download_button(
        f"📥 Download Laporan Penarikan {loc_tarik}",
        data=out_buf2,
        file_name=f"penarikan_{loc_tarik}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    )

st.markdown("---")
st.info("💡 **Tips:** Gunakan tab 'Input Porsi' untuk menghitung kebutuhan, lalu tab 'Penarikan Barang' untuk mencatat pengambilan stok.")
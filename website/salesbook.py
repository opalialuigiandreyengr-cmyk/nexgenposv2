"""
Sales Book Report Module
Contains all sales book export functions for different discount types:
- Senior Citizen Sales Book
- PWD (Persons with Disability) Sales Book
- Medal of Valor Sales Book
- Solo Parent Sales Book
- National Athletes and Coaches Sales Book
"""

from flask import request, send_file, after_this_request
from flask_login import login_required, current_user
from .models import Settlement, Order, db
from datetime import datetime, timedelta
import tempfile
import os
import time
import json
try:
    from openpyxl import Workbook, load_workbook
except ImportError:
    pass
try:
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    pass
try:
    from openpyxl.utils import get_column_letter
except ImportError:
    pass
from .receipt_content import get_report_header_fields


def _report_header():
    return get_report_header_fields()


def export_senior_citizen_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Senior Citizen Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements with senior discount for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with senior discounts (pure or mixed)
    senior_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'senior' in discount_type:
            senior_settlements.append(settlement)
    
    settlements = senior_settlements
    
    # Prepare data for senior citizen entries
    sc_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Parse senior citizen names and IDs
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        ids = [i.strip() for i in (settlement.discount_id or '').split(',') if i.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        # Extract SC ID (remove type prefix if exists)
        sc_ids = []
        for id_val in ids:
            if ':' in id_val:
                prefix, id_num = id_val.split(':', 1)
                prefix_normalized = prefix.strip().lower()
                if prefix_normalized == 'senior':
                    sc_ids.append(id_num.strip())
                else:
                    sc_ids.append(id_val)
            else:
                sc_ids.append(id_val)
        
        # Handle mixed discounts
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        # Get discount breakdown for mixed discounts
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        # Process only Senior Citizen entries
        sc_entries = []
        for i, name in enumerate(names):
            if name.lower().startswith('senior:'): 
                sc_name = name.split(':', 1)[1].strip() if ':' in name else name.strip()
                sc_id = sc_ids[i] if i < len(sc_ids) else ''
                sc_entries.append((sc_name, sc_id))
            elif not is_mixed_discount:
                sc_name = name
                sc_id = sc_ids[i] if i < len(sc_ids) else ''
                sc_entries.append((sc_name, sc_id))
        
        if not sc_entries:
            continue
        
        # Process each Senior Citizen entry
        for sc_name, sc_id in sc_entries:
            vat_exempt_eligible = ['senior', 'pwd', 'solo_parent']
            vat_exempt_count = len([d for d in discount_types if d.lower() in vat_exempt_eligible])
            
            total_vat_exempt = settlement.vat_exempt_sale or 0
            if vat_exempt_count > 0:
                vat_exempt_sales = total_vat_exempt / vat_exempt_count
            else:
                vat_exempt_sales = 0
            
            # Get discount amount for this Senior Citizen
            if is_mixed_discount and discount_breakdown and 'senior' in discount_breakdown:
                discount_amount = discount_breakdown.get('senior', 0)
                sc_count = len([i for i in discount_types if i.lower() == 'senior'])
                if sc_count > 1:
                    discount_amount = discount_amount / sc_count
            else:
                total_discount = settlement.discount_amount or 0
                sc_count = len([i for i in discount_types if i.lower() == 'senior'])
                if sc_count > 1:
                    discount_amount = total_discount / sc_count
                else:
                    discount_amount = total_discount
            
            sales_inclusive_vat = vat_exempt_sales * 1.12
            calculated_vat = (sales_inclusive_vat / 1.12) * 0.12
            
            discount_5pct = 0
            discount_20pct = 0
            if discount_amount > 0:
                discount_20pct = discount_amount
            
            net_sales = sales_inclusive_vat - calculated_vat - discount_amount
            
            sc_data.append({
                'date': order.timestamp.strftime('%m/%d/%Y'),
                'sc_name': sc_name,
                'osca_id': sc_id,
                'sc_tin': '',
                'si_or_number': order.invoice_no or '',
                'sales_inclusive_vat': sales_inclusive_vat,
                'vat_amount': calculated_vat,
                'vat_exempt_sales': vat_exempt_sales,
                'discount_5pct': discount_5pct,
                'discount_20pct': discount_20pct,
                'net_sales': net_sales
            })
    
    return create_sales_book_excel(sc_data, 'SC Sales Book', 'SENIOR CITIZEN SALES BOOK/REPORT',
                                  'senior_citizen_sales_book', from_date, to_date, report_date)


def export_pwd_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Persons with Disability Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with PWD discounts
    pwd_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'pwd' in discount_type:
            pwd_settlements.append(settlement)
    
    settlements = pwd_settlements
    
    # Prepare data for PWD entries
    pwd_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        ids = [i.strip() for i in (settlement.discount_id or '').split(',') if i.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        pwd_indices = [i for i, d in enumerate(discount_types) if d.lower() == 'pwd']
        
        if not pwd_indices:
            continue
        
        pwd_ids = []
        for id_val in ids:
            if ':' in id_val:
                prefix, id_num = id_val.split(':', 1)
                prefix_normalized = prefix.strip().lower()
                if prefix_normalized == 'pwd':
                    pwd_ids.append(id_num.strip())
                else:
                    pwd_ids.append(id_val)
            else:
                pwd_ids.append(id_val)
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        # Process only PWD entries
        for pwd_idx in pwd_indices:
            raw_name = names[pwd_idx] if pwd_idx < len(names) else ''
            if ':' in raw_name:
                prefix, pwd_name = raw_name.split(':', 1)
                if prefix.strip().lower() == 'pwd':
                    pwd_name = pwd_name.strip()
                else:
                    pwd_name = raw_name
            else:
                pwd_name = raw_name
            
            pwd_id = pwd_ids[pwd_idx] if pwd_idx < len(pwd_ids) else ''
            
            vat_exempt_eligible = ['senior', 'pwd', 'solo_parent']
            vat_exempt_count = len([d for d in discount_types if d.lower() in vat_exempt_eligible])
            
            total_vat_exempt = settlement.vat_exempt_sale or 0
            if vat_exempt_count > 0:
                vat_exempt_sales = total_vat_exempt / vat_exempt_count
            else:
                vat_exempt_sales = 0
            
            if is_mixed_discount and discount_breakdown and 'pwd' in discount_breakdown:
                discount_amount = discount_breakdown.get('pwd', 0)
                pwd_count = len([i for i in discount_types if i.lower() == 'pwd'])
                if pwd_count > 1:
                    discount_amount = discount_amount / pwd_count
            else:
                total_discount = settlement.discount_amount or 0
                pwd_count = len([i for i in discount_types if i.lower() == 'pwd'])
                if pwd_count > 1:
                    discount_amount = total_discount / pwd_count
                else:
                    discount_amount = total_discount
            
            sales_inclusive_vat = vat_exempt_sales * 1.12
            calculated_vat = (sales_inclusive_vat / 1.12) * 0.12
            
            discount_5pct = 0
            discount_20pct = 0
            if discount_amount > 0:
                discount_20pct = discount_amount
            
            net_sales = sales_inclusive_vat - calculated_vat - discount_amount
            
            pwd_data.append({
                'date': order.timestamp.strftime('%m/%d/%Y'),
                'pwd_name': pwd_name,
                'pwd_id': pwd_id,
                'pwd_tin': '',
                'si_or_number': order.invoice_no or '',
                'sales_inclusive_vat': sales_inclusive_vat,
                'vat_amount': calculated_vat,
                'vat_exempt_sales': vat_exempt_sales,
                'discount_5pct': discount_5pct,
                'discount_20pct': discount_20pct,
                'net_sales': net_sales
            })
    
    return create_sales_book_excel(pwd_data, 'PWD Sales Book', 'PERSONS WITH DISABILITY SALES BOOK/REPORT',
                                  'pwd_sales_book', from_date, to_date, report_date)


def export_mov_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Medal of Valor Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with MOV discounts
    mov_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'mov' in discount_type or 'medal_of_valor' in discount_type:
            mov_settlements.append(settlement)
    
    settlements = mov_settlements
    
    # Prepare data for MOV entries
    mov_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        ids = [i.strip() for i in (settlement.discount_id or '').split(',') if i.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        mov_indices = [i for i, d in enumerate(discount_types) if d.lower() in ['mov', 'medal_of_valor']]
        
        if not mov_indices:
            continue
        
        mov_ids = []
        for id_val in ids:
            if ':' in id_val:
                prefix, id_num = id_val.split(':', 1)
                prefix_normalized = prefix.strip().lower()
                if prefix_normalized in ['mov', 'medal_of_valor']:
                    mov_ids.append(id_num.strip())
                else:
                    mov_ids.append(id_val)
            else:
                mov_ids.append(id_val)
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        mov_count = len([i for i in discount_types if i.lower() in ['mov', 'medal_of_valor']])
        
        if is_mixed_discount and discount_breakdown and 'mov' in discount_breakdown:
            total_mov_discount = discount_breakdown.get('mov', 0)
        elif is_mixed_discount and discount_breakdown and 'medal_of_valor' in discount_breakdown:
            total_mov_discount = discount_breakdown.get('medal_of_valor', 0)
        else:
            total_mov_discount = settlement.discount_amount or 0
        
        if not is_mixed_discount and mov_count > 0:
            for idx, name in enumerate(names):
                raw_name = name
                if ':' in raw_name:
                    prefix, mov_name = raw_name.split(':', 1)
                    if prefix.strip().lower() in ['mov', 'medal_of_valor']:
                        mov_name = mov_name.strip()
                    else:
                        mov_name = raw_name
                else:
                    mov_name = raw_name
                
                mov_id = mov_ids[idx] if idx < len(mov_ids) else ''
                
                discount_amount = total_mov_discount / len(names) if len(names) > 0 else 0
                gross_sales = (discount_amount / 0.2) * 1.12 if discount_amount > 0 else 0
                net_sales = gross_sales - discount_amount
                
                mov_data.append({
                    'date': order.timestamp.strftime('%m/%d/%Y'),
                    'mov_name': mov_name,
                    'mov_id': mov_id,
                    'si_or_number': order.invoice_no or '',
                    'gross_sales': gross_sales,
                    'sales_discount': discount_amount,
                    'net_sales': net_sales
                })
        else:
            for mov_idx in mov_indices:
                raw_name = names[mov_idx] if mov_idx < len(names) else ''
                if ':' in raw_name:
                    prefix, mov_name = raw_name.split(':', 1)
                    if prefix.strip().lower() in ['mov', 'medal_of_valor']:
                        mov_name = mov_name.strip()
                    else:
                        mov_name = raw_name
                else:
                    mov_name = raw_name
                
                mov_id = mov_ids[mov_idx] if mov_idx < len(mov_ids) else ''
                
                discount_amount = total_mov_discount / mov_count if mov_count > 0 else 0
                gross_sales = (discount_amount / 0.2) * 1.12 if discount_amount > 0 else 0
                net_sales = gross_sales - discount_amount
                
                mov_data.append({
                    'date': order.timestamp.strftime('%m/%d/%Y'),
                    'mov_name': mov_name,
                    'mov_id': mov_id,
                    'si_or_number': order.invoice_no or '',
                    'gross_sales': gross_sales,
                    'sales_discount': discount_amount,
                    'net_sales': net_sales
                })
    
    return create_simple_sales_book_excel(mov_data, 'MOV Sales Book', 'MEDAL OF VALOR SALES BOOK/REPORT',
                                        'mov_sales_book', from_date, to_date, report_date)


def export_solo_parent_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Solo Parent Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with solo parent discounts
    sp_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'solo_parent' in discount_type or 'solo parent' in discount_type:
            sp_settlements.append(settlement)
    
    settlements = sp_settlements
    
    # Prepare data for solo parent entries
    sp_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        ids = [i.strip() for i in (settlement.discount_id or '').split(',') if i.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        sp_indices = [i for i, d in enumerate(discount_types) if d.lower() in ['solo_parent', 'solo parent']]
        
        if not sp_indices:
            continue
        
        sp_ids = []
        for id_val in ids:
            if ':' in id_val:
                prefix, id_num = id_val.split(':', 1)
                prefix_normalized = prefix.strip().lower()
                if prefix_normalized in ['solo_parent', 'solo parent']:
                    sp_ids.append(id_num.strip())
                else:
                    sp_ids.append(id_val)
            else:
                sp_ids.append(id_val)
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        # Process only Solo Parent entries
        sp_entries = []
        for i, name in enumerate(names):
            if any(name.lower().startswith(prefix) for prefix in ['solo_parent:', 'solo parent:']):
                sp_name = name.split(':', 1)[1].strip() if ':' in name else name.strip()
                sp_id = sp_ids[i] if i < len(sp_ids) else ''
                sp_entries.append((sp_name, sp_id))
            elif not is_mixed_discount:
                sp_name = name
                sp_id = sp_ids[i] if i < len(sp_ids) else ''
                sp_entries.append((sp_name, sp_id))
        
        if not sp_entries:
            continue
        
        # Process each Solo Parent entry
        for sp_name, sp_id in sp_entries:
            vat_exempt_eligible = ['senior', 'pwd', 'solo_parent']
            vat_exempt_count = len([d for d in discount_types if d.lower() in vat_exempt_eligible])
            
            total_vat_exempt = settlement.vat_exempt_sale or 0
            if vat_exempt_count > 0:
                vat_exempt_sales = total_vat_exempt / vat_exempt_count
            else:
                vat_exempt_sales = 0
            
            if is_mixed_discount and discount_breakdown and 'solo_parent' in discount_breakdown:
                discount_amount = discount_breakdown.get('solo_parent', 0)
                sp_count = len([i for i in discount_types if i.lower() == 'solo_parent'])
                if sp_count > 1:
                    discount_amount = discount_amount / sp_count
            else:
                total_discount = settlement.discount_amount or 0
                sp_count = len([i for i in discount_types if i.lower() in ['solo_parent', 'solo parent']])
                if sp_count > 1:
                    discount_amount = total_discount / sp_count
                else:
                    discount_amount = total_discount
            
            sales_inclusive_vat = vat_exempt_sales * 1.12
            calculated_vat = (sales_inclusive_vat / 1.12) * 0.12
            
            discount_5pct = 0
            discount_10pct = 0
            if discount_amount > 0:
                discount_10pct = discount_amount
            
            net_sales = sales_inclusive_vat - calculated_vat - discount_amount
            
            sp_data.append({
                'date': order.timestamp.strftime('%m/%d/%Y'),
                'sp_name': sp_name,
                'spic_id': sp_id,
                'sp_tin': '',
                'si_or_number': order.invoice_no or '',
                'sales_inclusive_vat': sales_inclusive_vat,
                'vat_amount': calculated_vat,
                'vat_exempt_sales': vat_exempt_sales,
                'discount_5pct': discount_5pct,
                'discount_10pct': discount_10pct,
                'net_sales': net_sales
            })
    
    return create_solo_parent_excel(sp_data, 'SP Sales Book', 'SOLO PARENT SALES BOOK/REPORT',
                                   'solo_parent_sales_book', from_date, to_date, report_date)


def export_athlete_coaches_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export National Athletes and Coaches Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with athlete discounts
    athlete_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'athlete' in discount_type:
            athlete_settlements.append(settlement)
    
    settlements = athlete_settlements
    
    # Prepare data for athlete entries
    athlete_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        ids = [i.strip() for i in (settlement.discount_id or '').split(',') if i.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        athlete_indices = [i for i, d in enumerate(discount_types) if d.lower() == 'athlete']
        
        if not athlete_indices:
            continue
        
        athlete_ids = []
        for id_val in ids:
            if ':' in id_val:
                prefix, id_num = id_val.split(':', 1)
                prefix_normalized = prefix.strip().lower()
                if prefix_normalized == 'athlete':
                    athlete_ids.append(id_num.strip())
                else:
                    athlete_ids.append(id_val)
            else:
                athlete_ids.append(id_val)
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        athlete_count = len([i for i in discount_types if i.lower() == 'athlete'])
        
        if is_mixed_discount and discount_breakdown and 'athlete' in discount_breakdown:
            total_athlete_discount = discount_breakdown.get('athlete', 0)
        else:
            total_athlete_discount = settlement.discount_amount or 0
        
        if not is_mixed_discount and athlete_count > 0:
            for idx, name in enumerate(names):
                raw_name = name
                if ':' in raw_name:
                    prefix, athlete_name = raw_name.split(':', 1)
                    if prefix.strip().lower() == 'athlete':
                        athlete_name = athlete_name.strip()
                    else:
                        athlete_name = raw_name
                else:
                    athlete_name = raw_name
                
                athlete_id = athlete_ids[idx] if idx < len(athlete_ids) else ''
                
                discount_amount = total_athlete_discount / len(names) if len(names) > 0 else 0
                gross_sales = (discount_amount / 0.2) * 1.12 if discount_amount > 0 else 0
                net_sales = gross_sales - discount_amount
                
                athlete_data.append({
                    'date': order.timestamp.strftime('%m/%d/%Y'),
                    'athlete_name': athlete_name,
                    'pnstm_id': athlete_id,
                    'si_or_number': order.invoice_no or '',
                    'gross_sales': gross_sales,
                    'sales_discount': discount_amount,
                    'net_sales': net_sales
                })
        else:
            for athlete_idx in athlete_indices:
                raw_name = names[athlete_idx] if athlete_idx < len(names) else ''
                if ':' in raw_name:
                    prefix, athlete_name = raw_name.split(':', 1)
                    if prefix.strip().lower() == 'athlete':
                        athlete_name = athlete_name.strip()
                    else:
                        athlete_name = raw_name
                else:
                    athlete_name = raw_name
                
                athlete_id = athlete_ids[athlete_idx] if athlete_idx < len(athlete_ids) else ''
                
                discount_amount = total_athlete_discount / athlete_count if athlete_count > 0 else 0
                gross_sales = (discount_amount / 0.2) * 1.12 if discount_amount > 0 else 0
                net_sales = gross_sales - discount_amount
                
                athlete_data.append({
                    'date': order.timestamp.strftime('%m/%d/%Y'),
                    'athlete_name': athlete_name,
                    'pnstm_id': athlete_id,
                    'si_or_number': order.invoice_no or '',
                    'gross_sales': gross_sales,
                    'sales_discount': discount_amount,
                    'net_sales': net_sales
                })
    
    return create_simple_sales_book_excel(athlete_data, 'Athlete Sales Book', 'NATIONAL ATHLETES AND COACHES SALES BOOK/REPORT',
                                        'athlete_coaches_sales_book', from_date, to_date, report_date)


def create_sales_book_excel(data, sheet_title, title_text, filename_prefix, from_date, to_date, report_date):
    """Create Excel workbook for VAT-applicable sales books (Senior Citizen, PWD, Solo Parent)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:K1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:K2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:K3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10, bold=True)
    ws.row_dimensions[3].height = 20
    
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Row 12: Title
    ws.merge_cells('A12:K12')
    title_cell = ws['A12']
    title_cell.value = title_text
    title_cell.font = Font(bold=True, size=12, color="000000")
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    
    # Determine headers based on data
    is_senior = 'osca_id' in data[0] if data else False
    is_pwd = 'pwd_id' in data[0] if data else False
    is_sp = 'spic_id' in data[0] if data else False
    
    if is_senior:
        headers = [
            ('A13', 'Date'),
            ('B13', 'Name of Senior Citizen (SC)'),
            ('C13', 'OSCA ID No./ SC ID No.'),
            ('D13', 'SC TIN'),
            ('E13', 'SI Number'),
            ('F13', 'Sales (inclusive of VAT)'),
            ('G13', 'VAT Amount'),
            ('H13', 'VAT Exempt Sales'),
            ('I13', 'Discount 5%'),
            ('J13', 'Discount 20%'),
            ('K13', 'Net Sales')
        ]
        key_map = {'osca_id': 'C', 'sc_tin': 'D', 'sales_inclusive_vat': 'F', 'vat_amount': 'G', 
                   'vat_exempt_sales': 'H', 'discount_5pct': 'I', 'discount_20pct': 'J'}
        name_key = 'sc_name'
    elif is_pwd:
        headers = [
            ('A13', 'Date'),
            ('B13', 'Name of Person with Disability (PWD)'),
            ('C13', 'PWD ID No.'),
            ('D13', 'PWD TIN'),
            ('E13', 'SI Number'),
            ('F13', 'Sales (inclusive of VAT)'),
            ('G13', 'VAT Amount'),
            ('H13', 'VAT Exempt Sales'),
            ('I13', 'Discount 5%'),
            ('J13', 'Discount 20%'),
            ('K13', 'Net Sales')
        ]
        key_map = {'pwd_id': 'C', 'pwd_tin': 'D', 'sales_inclusive_vat': 'F', 'vat_amount': 'G',
                   'vat_exempt_sales': 'H', 'discount_5pct': 'I', 'discount_20pct': 'J'}
        name_key = 'pwd_name'
    elif is_sp:
        headers = [
            ('A13', 'Date'),
            ('B13', 'Name of Solo Parent'),
            ('C13', 'SPIC ID No.'),
            ('D13', 'SP TIN'),
            ('E13', 'SI Number'),
            ('F13', 'Sales (inclusive of VAT)'),
            ('G13', 'VAT Amount'),
            ('H13', 'VAT Exempt Sales'),
            ('I13', 'Discount 5%'),
            ('J13', 'Discount 10%'),
            ('K13', 'Net Sales')
        ]
        key_map = {'spic_id': 'C', 'sp_tin': 'D', 'sales_inclusive_vat': 'F', 'vat_amount': 'G',
                   'vat_exempt_sales': 'H', 'discount_5pct': 'I', 'discount_10pct': 'J'}
        name_key = 'sp_name'
    else:
        # Default headers when no data (use PWD as fallback based on title_text)
        if 'PWD' in title_text or 'DISABILITY' in title_text:
            headers = [
                ('A13', 'Date'),
                ('B13', 'Name of Person with Disability (PWD)'),
                ('C13', 'PWD ID No.'),
                ('D13', 'PWD TIN'),
                ('E13', 'SI Number'),
                ('F13', 'Sales (inclusive of VAT)'),
                ('G13', 'VAT Amount'),
                ('H13', 'VAT Exempt Sales'),
                ('I13', 'Discount 5%'),
                ('J13', 'Discount 20%'),
                ('K13', 'Net Sales')
            ]
            key_map = {'pwd_id': 'C', 'pwd_tin': 'D', 'sales_inclusive_vat': 'F', 'vat_amount': 'G',
                       'vat_exempt_sales': 'H', 'discount_5pct': 'I', 'discount_20pct': 'J'}
            name_key = 'pwd_name'
        elif 'SENIOR' in title_text:
            headers = [
                ('A13', 'Date'),
                ('B13', 'Name of Senior Citizen (SC)'),
                ('C13', 'OSCA ID No./ SC ID No.'),
                ('D13', 'SC TIN'),
                ('E13', 'SI Number'),
                ('F13', 'Sales (inclusive of VAT)'),
                ('G13', 'VAT Amount'),
                ('H13', 'VAT Exempt Sales'),
                ('I13', 'Discount 5%'),
                ('J13', 'Discount 20%'),
                ('K13', 'Net Sales')
            ]
            key_map = {'osca_id': 'C', 'sc_tin': 'D', 'sales_inclusive_vat': 'F', 'vat_amount': 'G', 
                       'vat_exempt_sales': 'H', 'discount_5pct': 'I', 'discount_20pct': 'J'}
            name_key = 'sc_name'
        else:  # Solo Parent fallback
            headers = [
                ('A13', 'Date'),
                ('B13', 'Name of Solo Parent'),
                ('C13', 'SPIC ID No.'),
                ('D13', 'SP TIN'),
                ('E13', 'SI Number'),
                ('F13', 'Sales (inclusive of VAT)'),
                ('G13', 'VAT Amount'),
                ('H13', 'VAT Exempt Sales'),
                ('I13', 'Discount 5%'),
                ('J13', 'Discount 10%'),
                ('K13', 'Net Sales')
            ]
            key_map = {'spic_id': 'C', 'sp_tin': 'D', 'sales_inclusive_vat': 'F', 'vat_amount': 'G',
                       'vat_exempt_sales': 'H', 'discount_5pct': 'I', 'discount_10pct': 'J'}
            name_key = 'sp_name'
    
    # Apply header styles
    grey_fill = PatternFill(start_color='C0C0C0', end_color='C0C0C0', fill_type='solid')
    blue_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    
    for cell_ref, header_text in headers:
        cell = ws[cell_ref]
        cell.value = header_text
        
        if cell_ref[0] == 'F':
            cell.fill = blue_fill
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        elif cell_ref[0] in ['G', 'H']:
            cell.fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] in ['I', 'J']:
            cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
            cell.font = Font(bold=False, size=11, color='000000')
        else:
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    ws.row_dimensions[13].height = 25
    
    # Add data rows
    total_sb_rows = len(data)
    current_row = 14
    for r_idx, row_data in enumerate(data):
        try:
            from export_task_manager import report_progress
            report_progress(r_idx + 1, max(1, total_sb_rows), f"Processing Sales Book row {r_idx + 1} of {total_sb_rows}...")
        except Exception:
            pass
        ws.cell(row=current_row, column=1).value = row_data['date']
        ws.cell(row=current_row, column=2).value = row_data[name_key]
        ws.cell(row=current_row, column=3).value = row_data[list(key_map.keys())[0]]
        ws.cell(row=current_row, column=4).value = row_data[list(key_map.keys())[1]]
        ws.cell(row=current_row, column=5).value = row_data['si_or_number']
        ws.cell(row=current_row, column=6).value = row_data['sales_inclusive_vat']
        ws.cell(row=current_row, column=7).value = row_data['vat_amount']
        ws.cell(row=current_row, column=8).value = row_data['vat_exempt_sales']
        ws.cell(row=current_row, column=9).value = row_data['discount_5pct']
        ws.cell(row=current_row, column=10).value = row_data.get('discount_20pct', row_data.get('discount_10pct', 0))
        ws.cell(row=current_row, column=11).value = row_data['net_sales']
        current_row += 1
    
    # Format numeric columns
    for row in range(14, current_row):
        for col in [6, 7, 8, 9, 10, 11]:
            ws.cell(row=row, column=col).number_format = '#,##0.00'
    
    # Set column widths
    column_widths = [12, 25, 20, 15, 15, 18, 15, 18, 12, 12, 15]
    for col, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    
    wb.save(tmp_filename)
    
    # Apply protection
    wb = load_workbook(tmp_filename)
    ws = wb[sheet_title]
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def export_regular_discount_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Regular Discount Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with regular discounts
    regular_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'regular' in discount_type:
            regular_settlements.append(settlement)
    
    settlements = regular_settlements
    
    # Prepare data for regular discount entries
    regular_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Get discount percentage for this settlement
        discount_percent = settlement.regular_discount_percent or 0
        
        # Calculate discount amount from settlement
        discount_amount = settlement.discount_amount or 0
        
        # Calculate gross sales (order total before discount)
        gross_sales = order.total or 0
        net_sales = gross_sales - discount_amount
        
        # Get customer name from order
        customer_name = order.customer_name or ''
        
        regular_data.append({
            'date': order.timestamp.strftime('%m/%d/%Y'),
            'customer_name': customer_name,
            'si_or_number': order.invoice_no or '',
            'gross_sales': gross_sales,
            'discount_percent': discount_percent,
            'sales_discount': discount_amount,
            'net_sales': net_sales
        })
    
    return create_regular_discount_excel(regular_data, 'Regular Discount Sales Book', 'REGULAR DISCOUNT SALES BOOK/REPORT',
                                        'regular_discount_sales_book', from_date, to_date, report_date)
    """Export Regular Discount Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with regular discounts
    regular_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'regular' in discount_type:
            regular_settlements.append(settlement)
    
    settlements = regular_settlements
    
    # Prepare data for regular discount entries
    regular_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Get discount percentage for this settlement
        discount_percent = settlement.regular_discount_percent or 0
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        # Get regular discount indices
        regular_indices = [i for i, d in enumerate(discount_types) if d.lower() == 'regular']
        
        if not regular_indices:
            continue
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        # Process regular discount entries
        regular_entries = []
        for i, name in enumerate(names):
            if i in regular_indices:
                regular_name = name.split(':', 1)[1].strip() if ':' in name else name.strip()
                regular_entries.append(regular_name)
            elif not is_mixed_discount and not any(dt.lower() in ['senior', 'pwd', 'solo_parent', 'athlete', 'medal_of_valor'] for dt in discount_types):
                # If not mixed and only regular discount
                regular_entries.append(name)
        
        # If no name entries but has regular discount, add one entry
        if not regular_entries and regular_indices:
            regular_entries.append('')
        
        # Process each regular discount entry
        for regular_name in regular_entries:
            # Calculate discount amount
            if is_mixed_discount and discount_breakdown and 'regular' in discount_breakdown:
                discount_amount = discount_breakdown.get('regular', 0)
                regular_count = len(regular_indices)
                if regular_count > 1:
                    discount_amount = discount_amount / regular_count
            else:
                total_discount = settlement.discount_amount or 0
                regular_count = len(regular_indices)
                if regular_count > 1:
                    discount_amount = total_discount / regular_count
                else:
                    discount_amount = total_discount
            
            # Calculate gross sales (order total before discount)
            gross_sales = order.total or 0
            net_sales = gross_sales - discount_amount
            
            regular_data.append({
                'date': order.timestamp.strftime('%m/%d/%Y'),
                'name': regular_name,
                'si_or_number': order.invoice_no or '',
                'gross_sales': gross_sales,
                'discount_percent': discount_percent,
                'sales_discount': discount_amount,
                'net_sales': net_sales
            })
    
    return create_regular_discount_excel(regular_data, 'Regular Discount Sales Book', 'REGULAR DISCOUNT SALES BOOK/REPORT',
                                        'regular_discount_sales_book', from_date, to_date, report_date)


def create_regular_discount_excel(data, sheet_title, title_text, filename_prefix, from_date, to_date, report_date):
    """Create Excel workbook for Regular Discount Sales Book (no VAT exemption)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10, bold=True)
    ws.row_dimensions[3].height = 20
    
    # Add metadata
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Report title
    ws.merge_cells('A12:G12')
    ws['A12'] = title_text
    ws['A12'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A12'].font = Font(size=13, bold=True)
    ws.row_dimensions[12].height = 25
    
    # Table headers (Row 13) - Multi-color like NAAC/MOV
    headers = [
        ('A13', 'Date'),
        ('B13', 'Name of Customer'),
        ('C13', 'SI Number'),
        ('D13', 'Gross Sales/Receipts'),
        ('E13', 'Discount %'),
        ('F13', 'Sales Discount'),
        ('G13', 'Net Sales')
    ]
    
    # Define color fills
    grey_fill = PatternFill(start_color='C0C0C0', end_color='C0C0C0', fill_type='solid')
    blue_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
    orange_fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    green_fill = PatternFill(start_color='92D050', end_color='92D050', fill_type='solid')
    
    for cell_ref, header_text in headers:
        cell = ws[cell_ref]
        cell.value = header_text
        
        # Apply different colors based on column
        if cell_ref[0] == 'A':  # Date - Grey
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'B':  # Name of Customer - Blue
            cell.fill = blue_fill
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        elif cell_ref[0] == 'C':  # SI Number - Yellow
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'D':  # Gross Sales - Green
            cell.fill = green_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'E':  # Discount % - Orange
            cell.fill = orange_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'F':  # Sales Discount - Yellow
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        else:  # Net Sales - Grey
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side('thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[13].height = 25
    
    # Data rows
    current_row = 14
    total_gross_sales = 0
    total_sales_discount = 0
    total_net_sales = 0
    
    for entry in data:
        ws.cell(row=current_row, column=1).value = entry['date']
        ws.cell(row=current_row, column=2).value = entry['customer_name']
        ws.cell(row=current_row, column=3).value = entry['si_or_number']
        ws.cell(row=current_row, column=4).value = round(entry['gross_sales'], 2)
        ws.cell(row=current_row, column=5).value = f"{entry['discount_percent']}%"
        ws.cell(row=current_row, column=6).value = round(entry['sales_discount'], 2)
        ws.cell(row=current_row, column=7).value = round(entry['net_sales'], 2)
        
        total_gross_sales += entry['gross_sales']
        total_sales_discount += entry['sales_discount']
        total_net_sales += entry['net_sales']
        
        # Apply formatting to data cells
        for col in range(1, 8):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            
            if col in [1, 5]:  # Date and Discount % columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col in [4, 6, 7]:  # Numeric columns - right align with 2 decimals
                cell.alignment = Alignment(horizontal='right', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Text columns - left align
                cell.alignment = Alignment(horizontal='left', vertical='center')
        
        current_row += 1
    
    # Set column widths
    ws.column_dimensions['A'].width = 12   # Date
    ws.column_dimensions['B'].width = 25   # Name of Customer
    ws.column_dimensions['C'].width = 18   # SI Number
    ws.column_dimensions['D'].width = 18   # Gross Sales
    ws.column_dimensions['E'].width = 12   # Discount %
    ws.column_dimensions['F'].width = 18   # Sales Discount
    ws.column_dimensions['G'].width = 15   # Net Sales
    
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
    """Create Excel workbook for Regular Discount Sales Book (no VAT exemption)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10)
    ws.row_dimensions[3].height = 22
    
    # Add metadata
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Report title
    ws.merge_cells('A12:G12')
    ws['A12'] = title_text
    ws['A12'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A12'].font = Font(size=13, bold=True)
    ws.row_dimensions[12].height = 25
    
    # Date range
    ws.merge_cells('A13:G13')
    if from_date == to_date:
        ws['A13'] = f"Date: {report_date.strftime('%B %d, %Y')}"
    else:
        ws['A13'] = f"Period: {from_date.strftime('%B %d, %Y')} to {to_date.strftime('%B %d, %Y')}"
    ws['A13'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A13'].font = Font(size=10)
    ws.row_dimensions[13].height = 20
    
    # Table headers (Row 15)
    headers = ['Date', 'Name', 'SI/OR Number', 'Gross Sales', 'Discount %', 'Sales Discount', 'Net Sales']
    header_row = 15
    
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.value = header
        cell.font = Font(bold=True, size=10)
        cell.fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
        cell.font = Font(bold=True, size=10, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[header_row].height = 30
    
    # Data rows
    current_row = header_row + 1
    total_gross_sales = 0
    total_sales_discount = 0
    total_net_sales = 0
    
    for entry in data:
        ws.cell(row=current_row, column=1).value = entry['date']
        ws.cell(row=current_row, column=2).value = entry['name']
        ws.cell(row=current_row, column=3).value = entry['si_or_number']
        ws.cell(row=current_row, column=4).value = round(entry['gross_sales'], 2)
        ws.cell(row=current_row, column=5).value = f"{entry['discount_percent']}%"
        ws.cell(row=current_row, column=6).value = round(entry['sales_discount'], 2)
        ws.cell(row=current_row, column=7).value = round(entry['net_sales'], 2)
        
        total_gross_sales += entry['gross_sales']
        total_sales_discount += entry['sales_discount']
        total_net_sales += entry['net_sales']
        
        # Apply formatting to data cells
        for col in range(1, 8):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            
            if col in [1, 5]:  # Date and Discount % columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col in [4, 6, 7]:  # Numeric columns - right align with 2 decimals
                cell.alignment = Alignment(horizontal='right', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Text columns - left align
                cell.alignment = Alignment(horizontal='left', vertical='center')
        
        current_row += 1
    
    # Add totals row
    totals_row = current_row
    ws.merge_cells(f'A{totals_row}:C{totals_row}')
    ws.cell(row=totals_row, column=1).value = 'TOTAL'
    ws.cell(row=totals_row, column=4).value = round(total_gross_sales, 2)
    ws.cell(row=totals_row, column=5).value = ''  # No total for discount %
    ws.cell(row=totals_row, column=6).value = round(total_sales_discount, 2)
    ws.cell(row=totals_row, column=7).value = round(total_net_sales, 2)
    
    # Apply formatting to totals row
    for col in range(1, 8):
        cell = ws.cell(row=totals_row, column=col)
        cell.font = Font(bold=True, size=10, color='FFFFFF')
        cell.fill = PatternFill(start_color='333333', end_color='333333', fill_type='solid')
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
        
        if col in [4, 6, 7]:  # Numeric columns
            cell.alignment = Alignment(horizontal='right', vertical='center')
            cell.number_format = '#,##0.00'
        else:
            cell.alignment = Alignment(horizontal='center', vertical='center')
    
    # Set column widths
    ws.column_dimensions['A'].width = 12   # Date
    ws.column_dimensions['B'].width = 25   # Name
    ws.column_dimensions['C'].width = 18   # SI/OR Number
    ws.column_dimensions['D'].width = 15   # Gross Sales
    ws.column_dimensions['E'].width = 12   # Discount %
    ws.column_dimensions['F'].width = 15   # Sales Discount
    ws.column_dimensions['G'].width = 15   # Net Sales
    
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def create_simple_sales_book_excel(data, sheet_title, title_text, filename_prefix, from_date, to_date, report_date):
    """Create Excel workbook for simple sales books (MOV, Athlete)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10, bold=True)
    ws.row_dimensions[3].height = 20
    
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Row 12: Title
    ws.merge_cells('A12:G12')
    title_cell = ws['A12']
    title_cell.value = title_text
    title_cell.font = Font(bold=True, size=12, color="000000")
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    
    # Headers
    is_athlete = 'athlete_name' in data[0] if data else False
    
    if is_athlete:
        name_label = 'Name of National Athlete/Coach'
        id_label = 'PNSTM ID No.'
        name_key = 'athlete_name'
        id_key = 'pnstm_id'
    else:  # MOV
        name_label = 'Name of Medal of Valor Recipient'
        id_label = 'MOV ID No.'
        name_key = 'mov_name'
        id_key = 'mov_id'
    
    headers = [
        ('A13', 'Date'),
        ('B13', name_label),
        ('C13', id_label),
        ('D13', 'SI Number'),
        ('E13', 'Gross Sales/Receipts'),
        ('F13', 'Sales Discount'),
        ('G13', 'Net Sales')
    ]
    
    # Apply header styles
    grey_fill = PatternFill(start_color='C0C0C0', end_color='C0C0C0', fill_type='solid')
    blue_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
    green_fill = PatternFill(start_color='92D050', end_color='92D050', fill_type='solid')
    
    for cell_ref, header_text in headers:
        cell = ws[cell_ref]
        cell.value = header_text
        
        if cell_ref[0] == 'A':
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'B':
            cell.fill = blue_fill
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        elif cell_ref[0] == 'C':
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'D':
            cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'E':
            cell.fill = green_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'F':
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        else:
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    ws.row_dimensions[13].height = 25
    
    # Add data rows
    total_sb_rows = len(data)
    current_row = 14
    for r_idx, row_data in enumerate(data):
        try:
            from export_task_manager import report_progress
            report_progress(r_idx + 1, max(1, total_sb_rows), f"Processing Sales Book row {r_idx + 1} of {total_sb_rows}...")
        except Exception:
            pass
        ws.cell(row=current_row, column=1).value = row_data['date']
        ws.cell(row=current_row, column=2).value = row_data[name_key]
        ws.cell(row=current_row, column=3).value = row_data[id_key]
        ws.cell(row=current_row, column=4).value = row_data['si_or_number']
        ws.cell(row=current_row, column=5).value = row_data['gross_sales']
        ws.cell(row=current_row, column=6).value = row_data['sales_discount']
        ws.cell(row=current_row, column=7).value = row_data['net_sales']
        current_row += 1
    
    # Format numeric columns
    for row in range(14, current_row):
        for col in [5, 6, 7]:
            ws.cell(row=row, column=col).number_format = '#,##0.00'
    
    # Set column widths
    column_widths = [12, 30, 15, 15, 18, 18, 15]
    for col, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    
    wb.save(tmp_filename)
    
    # Apply protection
    wb = load_workbook(tmp_filename)
    ws = wb[sheet_title]
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def export_regular_discount_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Regular Discount Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with regular discounts
    regular_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'regular' in discount_type:
            regular_settlements.append(settlement)
    
    settlements = regular_settlements
    
    # Prepare data for regular discount entries
    regular_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Get discount percentage for this settlement
        discount_percent = settlement.regular_discount_percent or 0
        
        # Calculate discount amount from settlement
        discount_amount = settlement.discount_amount or 0
        
        # Calculate gross sales (order total before discount)
        gross_sales = order.total or 0
        net_sales = gross_sales - discount_amount
        
        # Get customer name from order
        customer_name = order.customer_name or ''
        
        regular_data.append({
            'date': order.timestamp.strftime('%m/%d/%Y'),
            'customer_name': customer_name,
            'si_or_number': order.invoice_no or '',
            'gross_sales': gross_sales,
            'discount_percent': discount_percent,
            'sales_discount': discount_amount,
            'net_sales': net_sales
        })
    
    return create_regular_discount_excel(regular_data, 'Regular Discount Sales Book', 'REGULAR DISCOUNT SALES BOOK/REPORT',
                                        'regular_discount_sales_book', from_date, to_date, report_date)
    """Export Regular Discount Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with regular discounts
    regular_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'regular' in discount_type:
            regular_settlements.append(settlement)
    
    settlements = regular_settlements
    
    # Prepare data for regular discount entries
    regular_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Get discount percentage for this settlement
        discount_percent = settlement.regular_discount_percent or 0
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        # Get regular discount indices
        regular_indices = [i for i, d in enumerate(discount_types) if d.lower() == 'regular']
        
        if not regular_indices:
            continue
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        # Process regular discount entries
        regular_entries = []
        for i, name in enumerate(names):
            if i in regular_indices:
                regular_name = name.split(':', 1)[1].strip() if ':' in name else name.strip()
                regular_entries.append(regular_name)
            elif not is_mixed_discount and not any(dt.lower() in ['senior', 'pwd', 'solo_parent', 'athlete', 'medal_of_valor'] for dt in discount_types):
                # If not mixed and only regular discount
                regular_entries.append(name)
        
        # If no name entries but has regular discount, add one entry
        if not regular_entries and regular_indices:
            regular_entries.append('')
        
        # Process each regular discount entry
        for regular_name in regular_entries:
            # Calculate discount amount
            if is_mixed_discount and discount_breakdown and 'regular' in discount_breakdown:
                discount_amount = discount_breakdown.get('regular', 0)
                regular_count = len(regular_indices)
                if regular_count > 1:
                    discount_amount = discount_amount / regular_count
            else:
                total_discount = settlement.discount_amount or 0
                regular_count = len(regular_indices)
                if regular_count > 1:
                    discount_amount = total_discount / regular_count
                else:
                    discount_amount = total_discount
            
            # Calculate gross sales (order total before discount)
            gross_sales = order.total or 0
            net_sales = gross_sales - discount_amount
            
            regular_data.append({
                'date': order.timestamp.strftime('%m/%d/%Y'),
                'name': regular_name,
                'si_or_number': order.invoice_no or '',
                'gross_sales': gross_sales,
                'discount_percent': discount_percent,
                'sales_discount': discount_amount,
                'net_sales': net_sales
            })
    
    return create_regular_discount_excel(regular_data, 'Regular Discount Sales Book', 'REGULAR DISCOUNT SALES BOOK/REPORT',
                                        'regular_discount_sales_book', from_date, to_date, report_date)


def create_regular_discount_excel(data, sheet_title, title_text, filename_prefix, from_date, to_date, report_date):
    """Create Excel workbook for Regular Discount Sales Book (no VAT exemption)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10, bold=True)
    ws.row_dimensions[3].height = 20
    
    # Add metadata
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Report title
    ws.merge_cells('A12:G12')
    ws['A12'] = title_text
    ws['A12'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A12'].font = Font(size=13, bold=True)
    ws.row_dimensions[12].height = 25
    
    # Table headers (Row 13) - Multi-color like NAAC/MOV
    headers = [
        ('A13', 'Date'),
        ('B13', 'Name of Customer'),
        ('C13', 'SI Number'),
        ('D13', 'Gross Sales/Receipts'),
        ('E13', 'Discount %'),
        ('F13', 'Sales Discount'),
        ('G13', 'Net Sales')
    ]
    
    # Define color fills
    grey_fill = PatternFill(start_color='C0C0C0', end_color='C0C0C0', fill_type='solid')
    blue_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
    orange_fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    green_fill = PatternFill(start_color='92D050', end_color='92D050', fill_type='solid')
    
    for cell_ref, header_text in headers:
        cell = ws[cell_ref]
        cell.value = header_text
        
        # Apply different colors based on column
        if cell_ref[0] == 'A':  # Date - Grey
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'B':  # Name of Customer - Blue
            cell.fill = blue_fill
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        elif cell_ref[0] == 'C':  # SI Number - Yellow
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'D':  # Gross Sales - Green
            cell.fill = green_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'E':  # Discount % - Orange
            cell.fill = orange_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'F':  # Sales Discount - Yellow
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        else:  # Net Sales - Grey
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side('thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[13].height = 25
    
    # Data rows
    current_row = 14
    total_gross_sales = 0
    total_sales_discount = 0
    total_net_sales = 0
    
    for entry in data:
        ws.cell(row=current_row, column=1).value = entry['date']
        ws.cell(row=current_row, column=2).value = entry['customer_name']
        ws.cell(row=current_row, column=3).value = entry['si_or_number']
        ws.cell(row=current_row, column=4).value = round(entry['gross_sales'], 2)
        ws.cell(row=current_row, column=5).value = f"{entry['discount_percent']}%"
        ws.cell(row=current_row, column=6).value = round(entry['sales_discount'], 2)
        ws.cell(row=current_row, column=7).value = round(entry['net_sales'], 2)
        
        total_gross_sales += entry['gross_sales']
        total_sales_discount += entry['sales_discount']
        total_net_sales += entry['net_sales']
        
        # Apply formatting to data cells
        for col in range(1, 8):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            
            if col in [1, 5]:  # Date and Discount % columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col in [4, 6, 7]:  # Numeric columns - right align with 2 decimals
                cell.alignment = Alignment(horizontal='right', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Text columns - left align
                cell.alignment = Alignment(horizontal='left', vertical='center')
        
        current_row += 1
    
    # Set column widths
    ws.column_dimensions['A'].width = 12   # Date
    ws.column_dimensions['B'].width = 25   # Name of Customer
    ws.column_dimensions['C'].width = 18   # SI Number
    ws.column_dimensions['D'].width = 18   # Gross Sales
    ws.column_dimensions['E'].width = 12   # Discount %
    ws.column_dimensions['F'].width = 18   # Sales Discount
    ws.column_dimensions['G'].width = 15   # Net Sales
    
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
    """Create Excel workbook for Regular Discount Sales Book (no VAT exemption)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10)
    ws.row_dimensions[3].height = 22
    
    # Add metadata
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Report title
    ws.merge_cells('A12:G12')
    ws['A12'] = title_text
    ws['A12'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A12'].font = Font(size=13, bold=True)
    ws.row_dimensions[12].height = 25
    
    # Date range
    ws.merge_cells('A13:G13')
    if from_date == to_date:
        ws['A13'] = f"Date: {report_date.strftime('%B %d, %Y')}"
    else:
        ws['A13'] = f"Period: {from_date.strftime('%B %d, %Y')} to {to_date.strftime('%B %d, %Y')}"
    ws['A13'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A13'].font = Font(size=10)
    ws.row_dimensions[13].height = 20
    
    # Table headers (Row 15)
    headers = ['Date', 'Name', 'SI/OR Number', 'Gross Sales', 'Discount %', 'Sales Discount', 'Net Sales']
    header_row = 15
    
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.value = header
        cell.font = Font(bold=True, size=10)
        cell.fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
        cell.font = Font(bold=True, size=10, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[header_row].height = 30
    
    # Data rows
    current_row = header_row + 1
    total_gross_sales = 0
    total_sales_discount = 0
    total_net_sales = 0
    
    for entry in data:
        ws.cell(row=current_row, column=1).value = entry['date']
        ws.cell(row=current_row, column=2).value = entry['name']
        ws.cell(row=current_row, column=3).value = entry['si_or_number']
        ws.cell(row=current_row, column=4).value = round(entry['gross_sales'], 2)
        ws.cell(row=current_row, column=5).value = f"{entry['discount_percent']}%"
        ws.cell(row=current_row, column=6).value = round(entry['sales_discount'], 2)
        ws.cell(row=current_row, column=7).value = round(entry['net_sales'], 2)
        
        total_gross_sales += entry['gross_sales']
        total_sales_discount += entry['sales_discount']
        total_net_sales += entry['net_sales']
        
        # Apply formatting to data cells
        for col in range(1, 8):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            
            if col in [1, 5]:  # Date and Discount % columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col in [4, 6, 7]:  # Numeric columns - right align with 2 decimals
                cell.alignment = Alignment(horizontal='right', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Text columns - left align
                cell.alignment = Alignment(horizontal='left', vertical='center')
        
        current_row += 1
    
    # Add totals row
    totals_row = current_row
    ws.merge_cells(f'A{totals_row}:C{totals_row}')
    ws.cell(row=totals_row, column=1).value = 'TOTAL'
    ws.cell(row=totals_row, column=4).value = round(total_gross_sales, 2)
    ws.cell(row=totals_row, column=5).value = ''  # No total for discount %
    ws.cell(row=totals_row, column=6).value = round(total_sales_discount, 2)
    ws.cell(row=totals_row, column=7).value = round(total_net_sales, 2)
    
    # Apply formatting to totals row
    for col in range(1, 8):
        cell = ws.cell(row=totals_row, column=col)
        cell.font = Font(bold=True, size=10, color='FFFFFF')
        cell.fill = PatternFill(start_color='333333', end_color='333333', fill_type='solid')
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
        
        if col in [4, 6, 7]:  # Numeric columns
            cell.alignment = Alignment(horizontal='right', vertical='center')
            cell.number_format = '#,##0.00'
        else:
            cell.alignment = Alignment(horizontal='center', vertical='center')
    
    # Set column widths
    ws.column_dimensions['A'].width = 12   # Date
    ws.column_dimensions['B'].width = 25   # Name
    ws.column_dimensions['C'].width = 18   # SI/OR Number
    ws.column_dimensions['D'].width = 15   # Gross Sales
    ws.column_dimensions['E'].width = 12   # Discount %
    ws.column_dimensions['F'].width = 15   # Sales Discount
    ws.column_dimensions['G'].width = 15   # Net Sales
    
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def create_solo_parent_excel(data, sheet_title, title_text, filename_prefix, from_date, to_date, report_date):
    """Create Excel workbook for Solo Parent sales book"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws['O1'] = _report_header()['company_name']
    ws['O2'] = _report_header()['address']
    ws['O3'] = _report_header()['vat']
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Row 12: Title
    ws.merge_cells('A12:K12')
    title_cell = ws['A12']
    title_cell.value = title_text
    title_cell.font = Font(bold=True, size=12, color="000000")
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[12].height = 20
    
    # Headers
    headers = [
        ('A13', 'Date'),
        ('B13', 'Name of Solo Parent'),
        ('C13', 'SPIC ID No.'),
        ('D13', 'SP TIN'),
        ('E13', 'SI Number'),
        ('F13', 'Sales (inclusive of VAT)'),
        ('G13', 'VAT Amount'),
        ('H13', 'VAT Exempt Sales'),
        ('I13', 'Discount 5%'),
        ('J13', 'Discount 10%'),
        ('K13', 'Net Sales')
    ]
    
    # Apply header styles
    grey_fill = PatternFill(start_color='C0C0C0', end_color='C0C0C0', fill_type='solid')
    blue_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    
    for cell_ref, header_text in headers:
        cell = ws[cell_ref]
        cell.value = header_text
        
        if cell_ref[0] == 'F':
            cell.fill = blue_fill
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        elif cell_ref[0] in ['G', 'H']:
            cell.fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] in ['I', 'J']:
            cell.fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
            cell.font = Font(bold=False, size=11, color='000000')
        else:
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                            top=Side(style='thin'), bottom=Side(style='thin'))
    
    ws.row_dimensions[13].height = 25
    
    # Add data rows
    total_sb_rows = len(data)
    current_row = 14
    for r_idx, row_data in enumerate(data):
        try:
            from export_task_manager import report_progress
            report_progress(r_idx + 1, max(1, total_sb_rows), f"Processing Sales Book row {r_idx + 1} of {total_sb_rows}...")
        except Exception:
            pass
        ws.cell(row=current_row, column=1).value = row_data['date']
        ws.cell(row=current_row, column=2).value = row_data['sp_name']
        ws.cell(row=current_row, column=3).value = row_data['spic_id']
        ws.cell(row=current_row, column=4).value = row_data['sp_tin']
        ws.cell(row=current_row, column=5).value = row_data['si_or_number']
        ws.cell(row=current_row, column=6).value = row_data['sales_inclusive_vat']
        ws.cell(row=current_row, column=7).value = row_data['vat_amount']
        ws.cell(row=current_row, column=8).value = row_data['vat_exempt_sales']
        ws.cell(row=current_row, column=9).value = row_data['discount_5pct']
        ws.cell(row=current_row, column=10).value = row_data['discount_10pct']
        ws.cell(row=current_row, column=11).value = row_data['net_sales']
        current_row += 1
    
    # Format numeric columns
    for row in range(14, current_row):
        for col in [6, 7, 8, 9, 10, 11]:
            ws.cell(row=row, column=col).number_format = '#,##0.00'
    
    # Set column widths
    column_widths = [12, 25, 20, 15, 15, 18, 15, 18, 12, 12, 15]
    for col, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    
    wb.save(tmp_filename)
    
    # Apply protection
    wb = load_workbook(tmp_filename)
    ws = wb[sheet_title]
    wb.security.workbookPassword = 'protected'
    wb.security.lockStructure = True
    ws.protection.sheet = True
    ws.protection.password = 'protected'
    ws.protection.enable()
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def export_regular_discount_sales_book_excel(from_date_str=None, to_date_str=None):
    """Export Regular Discount Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with regular discounts
    regular_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'regular' in discount_type:
            regular_settlements.append(settlement)
    
    settlements = regular_settlements
    
    # Prepare data for regular discount entries
    regular_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Get discount percentage for this settlement
        discount_percent = settlement.regular_discount_percent or 0
        
        # Calculate discount amount from settlement
        discount_amount = settlement.discount_amount or 0
        
        # Calculate gross sales (order total before discount)
        gross_sales = order.total or 0
        net_sales = gross_sales - discount_amount
        
        # Get customer name from order
        customer_name = order.customer_name or ''
        
        regular_data.append({
            'date': order.timestamp.strftime('%m/%d/%Y'),
            'customer_name': customer_name,
            'si_or_number': order.invoice_no or '',
            'gross_sales': gross_sales,
            'discount_percent': discount_percent,
            'sales_discount': discount_amount,
            'net_sales': net_sales
        })
    
    return create_regular_discount_excel(regular_data, 'Regular Discount Sales Book', 'REGULAR DISCOUNT SALES BOOK/REPORT',
                                        'regular_discount_sales_book', from_date, to_date, report_date)
    """Export Regular Discount Sales Book to Excel"""
    
    if from_date_str and to_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d')
            report_date = from_date
        except ValueError:
            from_date = datetime.today()
            to_date = datetime.today()
            report_date = datetime.today()
    else:
        from_date = datetime.today()
        to_date = datetime.today()
        report_date = datetime.today()
    
    # Get all settlements for the date range
    start_date = from_date.replace(hour=9, minute=0, second=0, microsecond=0)
    end_date = to_date.replace(hour=3, minute=59, second=59, microsecond=999999) + timedelta(days=1)
    
    settlements = Settlement.query.join(Order).filter(
        Settlement.timestamp >= start_date,
        Settlement.timestamp < end_date,
        Order.status == 'completed'
    ).order_by(Settlement.timestamp.asc()).all()
    
    # Filter settlements to only those with regular discounts
    regular_settlements = []
    for settlement in settlements:
        discount_type = (settlement.order_discount_type or '').lower()
        if 'regular' in discount_type:
            regular_settlements.append(settlement)
    
    settlements = regular_settlements
    
    # Prepare data for regular discount entries
    regular_data = []
    for settlement in settlements:
        order = settlement.order
        if not order:
            continue
        
        # Get discount percentage for this settlement
        discount_percent = settlement.regular_discount_percent or 0
        
        names = [n.strip() for n in (settlement.discount_name or '').split(',') if n.strip()]
        discount_types = [d.strip() for d in (settlement.order_discount_type or '').split(',') if d.strip()]
        
        # Get regular discount indices
        regular_indices = [i for i, d in enumerate(discount_types) if d.lower() == 'regular']
        
        if not regular_indices:
            continue
        
        discount_type_str = (settlement.order_discount_type or '').lower()
        is_mixed_discount = ',' in discount_type_str
        
        discount_breakdown = {}
        if is_mixed_discount and hasattr(settlement, 'discount_breakdown') and settlement.discount_breakdown:
            try:
                discount_breakdown = json.loads(settlement.discount_breakdown)
            except:
                discount_breakdown = {}
        
        # Process regular discount entries
        regular_entries = []
        for i, name in enumerate(names):
            if i in regular_indices:
                regular_name = name.split(':', 1)[1].strip() if ':' in name else name.strip()
                regular_entries.append(regular_name)
            elif not is_mixed_discount and not any(dt.lower() in ['senior', 'pwd', 'solo_parent', 'athlete', 'medal_of_valor'] for dt in discount_types):
                # If not mixed and only regular discount
                regular_entries.append(name)
        
        # If no name entries but has regular discount, add one entry
        if not regular_entries and regular_indices:
            regular_entries.append('')
        
        # Process each regular discount entry
        for regular_name in regular_entries:
            # Calculate discount amount
            if is_mixed_discount and discount_breakdown and 'regular' in discount_breakdown:
                discount_amount = discount_breakdown.get('regular', 0)
                regular_count = len(regular_indices)
                if regular_count > 1:
                    discount_amount = discount_amount / regular_count
            else:
                total_discount = settlement.discount_amount or 0
                regular_count = len(regular_indices)
                if regular_count > 1:
                    discount_amount = total_discount / regular_count
                else:
                    discount_amount = total_discount
            
            # Calculate gross sales (order total before discount)
            gross_sales = order.total or 0
            net_sales = gross_sales - discount_amount
            
            regular_data.append({
                'date': order.timestamp.strftime('%m/%d/%Y'),
                'name': regular_name,
                'si_or_number': order.invoice_no or '',
                'gross_sales': gross_sales,
                'discount_percent': discount_percent,
                'sales_discount': discount_amount,
                'net_sales': net_sales
            })
    
    return create_regular_discount_excel(regular_data, 'Regular Discount Sales Book', 'REGULAR DISCOUNT SALES BOOK/REPORT',
                                        'regular_discount_sales_book', from_date, to_date, report_date)


def create_regular_discount_excel(data, sheet_title, title_text, filename_prefix, from_date, to_date, report_date):
    """Create Excel workbook for Regular Discount Sales Book (no VAT exemption)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10, bold=True)
    ws.row_dimensions[3].height = 20
    
    # Add metadata
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Report title
    ws.merge_cells('A12:G12')
    ws['A12'] = title_text
    ws['A12'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A12'].font = Font(size=13, bold=True)
    ws.row_dimensions[12].height = 25
    
    # Table headers (Row 13) - Multi-color like NAAC/MOV
    headers = [
        ('A13', 'Date'),
        ('B13', 'Name of Customer'),
        ('C13', 'SI Number'),
        ('D13', 'Gross Sales/Receipts'),
        ('E13', 'Discount %'),
        ('F13', 'Sales Discount'),
        ('G13', 'Net Sales')
    ]
    
    # Define color fills
    grey_fill = PatternFill(start_color='C0C0C0', end_color='C0C0C0', fill_type='solid')
    blue_fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
    orange_fill = PatternFill(start_color='FFC000', end_color='FFC000', fill_type='solid')
    green_fill = PatternFill(start_color='92D050', end_color='92D050', fill_type='solid')
    
    for cell_ref, header_text in headers:
        cell = ws[cell_ref]
        cell.value = header_text
        
        # Apply different colors based on column
        if cell_ref[0] == 'A':  # Date - Grey
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'B':  # Name of Customer - Blue
            cell.fill = blue_fill
            cell.font = Font(bold=False, size=11, color='FFFFFF')
        elif cell_ref[0] == 'C':  # SI Number - Yellow
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'D':  # Gross Sales - Green
            cell.fill = green_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'E':  # Discount % - Orange
            cell.fill = orange_fill
            cell.font = Font(bold=False, size=11, color='000000')
        elif cell_ref[0] == 'F':  # Sales Discount - Yellow
            cell.fill = yellow_fill
            cell.font = Font(bold=False, size=11, color='000000')
        else:  # Net Sales - Grey
            cell.fill = grey_fill
            cell.font = Font(bold=False, size=11, color='000000')
        
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side('thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[13].height = 25
    
    # Data rows
    current_row = 14
    total_gross_sales = 0
    total_sales_discount = 0
    total_net_sales = 0
    
    for entry in data:
        ws.cell(row=current_row, column=1).value = entry['date']
        ws.cell(row=current_row, column=2).value = entry['customer_name']
        ws.cell(row=current_row, column=3).value = entry['si_or_number']
        ws.cell(row=current_row, column=4).value = round(entry['gross_sales'], 2)
        ws.cell(row=current_row, column=5).value = f"{entry['discount_percent']}%"
        ws.cell(row=current_row, column=6).value = round(entry['sales_discount'], 2)
        ws.cell(row=current_row, column=7).value = round(entry['net_sales'], 2)
        
        total_gross_sales += entry['gross_sales']
        total_sales_discount += entry['sales_discount']
        total_net_sales += entry['net_sales']
        
        # Apply formatting to data cells
        for col in range(1, 8):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            
            if col in [1, 5]:  # Date and Discount % columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col in [4, 6, 7]:  # Numeric columns - right align with 2 decimals
                cell.alignment = Alignment(horizontal='right', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Text columns - left align
                cell.alignment = Alignment(horizontal='left', vertical='center')
        
        current_row += 1
    
    # Set column widths
    ws.column_dimensions['A'].width = 12   # Date
    ws.column_dimensions['B'].width = 25   # Name of Customer
    ws.column_dimensions['C'].width = 18   # SI Number
    ws.column_dimensions['D'].width = 18   # Gross Sales
    ws.column_dimensions['E'].width = 12   # Discount %
    ws.column_dimensions['F'].width = 18   # Sales Discount
    ws.column_dimensions['G'].width = 15   # Net Sales
    
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
    """Create Excel workbook for Regular Discount Sales Book (no VAT exemption)"""
    
    tmp_filename = tempfile.mktemp(suffix='.xlsx')
    
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    
    # Add header information
    ws.merge_cells('A1:G1')
    ws['A1'] = _report_header()['company_name']
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].font = Font(size=11, bold=True)
    ws.row_dimensions[1].height = 22
    
    ws.merge_cells('A2:G2')
    ws['A2'] = _report_header()['address']
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A2'].font = Font(size=10)
    ws.row_dimensions[2].height = 22
    
    ws.merge_cells('A3:G3')
    ws['A3'] = _report_header()['vat']
    ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A3'].font = Font(size=10)
    ws.row_dimensions[3].height = 22
    
    # Add metadata
    ws['A5'] = _report_header()['software']
    ws['A6'] = _report_header()['min']
    ws['A7'] = _report_header()['sn']
    ws['A8'] = _report_header()['terminal']
    ws['A9'] = f"Date and Time Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"
    ws['A10'] = current_user.username if current_user.is_authenticated else 'Unknown'
    
    # Report title
    ws.merge_cells('A12:G12')
    ws['A12'] = title_text
    ws['A12'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A12'].font = Font(size=13, bold=True)
    ws.row_dimensions[12].height = 25
    
    # Date range
    ws.merge_cells('A13:G13')
    if from_date == to_date:
        ws['A13'] = f"Date: {report_date.strftime('%B %d, %Y')}"
    else:
        ws['A13'] = f"Period: {from_date.strftime('%B %d, %Y')} to {to_date.strftime('%B %d, %Y')}"
    ws['A13'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A13'].font = Font(size=10)
    ws.row_dimensions[13].height = 20
    
    # Table headers (Row 15)
    headers = ['Date', 'Name', 'SI/OR Number', 'Gross Sales', 'Discount %', 'Sales Discount', 'Net Sales']
    header_row = 15
    
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.value = header
        cell.font = Font(bold=True, size=10)
        cell.fill = PatternFill(start_color='0070C0', end_color='0070C0', fill_type='solid')
        cell.font = Font(bold=True, size=10, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
    
    ws.row_dimensions[header_row].height = 30
    
    # Data rows
    current_row = header_row + 1
    total_gross_sales = 0
    total_sales_discount = 0
    total_net_sales = 0
    
    for entry in data:
        ws.cell(row=current_row, column=1).value = entry['date']
        ws.cell(row=current_row, column=2).value = entry['name']
        ws.cell(row=current_row, column=3).value = entry['si_or_number']
        ws.cell(row=current_row, column=4).value = round(entry['gross_sales'], 2)
        ws.cell(row=current_row, column=5).value = f"{entry['discount_percent']}%"
        ws.cell(row=current_row, column=6).value = round(entry['sales_discount'], 2)
        ws.cell(row=current_row, column=7).value = round(entry['net_sales'], 2)
        
        total_gross_sales += entry['gross_sales']
        total_sales_discount += entry['sales_discount']
        total_net_sales += entry['net_sales']
        
        # Apply formatting to data cells
        for col in range(1, 8):
            cell = ws.cell(row=current_row, column=col)
            cell.border = Border(
                left=Side(style='thin', color='000000'),
                right=Side(style='thin', color='000000'),
                top=Side(style='thin', color='000000'),
                bottom=Side(style='thin', color='000000')
            )
            
            if col in [1, 5]:  # Date and Discount % columns - center align
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col in [4, 6, 7]:  # Numeric columns - right align with 2 decimals
                cell.alignment = Alignment(horizontal='right', vertical='center')
                cell.number_format = '#,##0.00'
            else:  # Text columns - left align
                cell.alignment = Alignment(horizontal='left', vertical='center')
        
        current_row += 1
    
    # Add totals row
    totals_row = current_row
    ws.merge_cells(f'A{totals_row}:C{totals_row}')
    ws.cell(row=totals_row, column=1).value = 'TOTAL'
    ws.cell(row=totals_row, column=4).value = round(total_gross_sales, 2)
    ws.cell(row=totals_row, column=5).value = ''  # No total for discount %
    ws.cell(row=totals_row, column=6).value = round(total_sales_discount, 2)
    ws.cell(row=totals_row, column=7).value = round(total_net_sales, 2)
    
    # Apply formatting to totals row
    for col in range(1, 8):
        cell = ws.cell(row=totals_row, column=col)
        cell.font = Font(bold=True, size=10, color='FFFFFF')
        cell.fill = PatternFill(start_color='333333', end_color='333333', fill_type='solid')
        cell.border = Border(
            left=Side(style='thin', color='000000'),
            right=Side(style='thin', color='000000'),
            top=Side(style='thin', color='000000'),
            bottom=Side(style='thin', color='000000')
        )
        
        if col in [4, 6, 7]:  # Numeric columns
            cell.alignment = Alignment(horizontal='right', vertical='center')
            cell.number_format = '#,##0.00'
        else:
            cell.alignment = Alignment(horizontal='center', vertical='center')
    
    # Set column widths
    ws.column_dimensions['A'].width = 12   # Date
    ws.column_dimensions['B'].width = 25   # Name
    ws.column_dimensions['C'].width = 18   # SI/OR Number
    ws.column_dimensions['D'].width = 15   # Gross Sales
    ws.column_dimensions['E'].width = 12   # Discount %
    ws.column_dimensions['F'].width = 15   # Sales Discount
    ws.column_dimensions['G'].width = 15   # Net Sales
    
    wb.save(tmp_filename)
    
    # Generate filename
    if from_date == to_date:
        filename = f"{filename_prefix}_{report_date.strftime('%Y-%m-%d')}.xlsx"
    else:
        filename = f"{filename_prefix}_{from_date.strftime('%Y-%m-%d')}_to_{to_date.strftime('%Y-%m-%d')}.xlsx"
    
    @after_this_request
    def remove_file(response):
        try:
            os.remove(tmp_filename)
        except Exception:
            pass
        return response
    
    time.sleep(0.1)
    
    # Log report export activity
    from .activity_logger import log_activity
    date_range_str = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}" if from_date != to_date else from_date.strftime('%Y-%m-%d')
    log_activity(
        'REPORT_DOWNLOAD',
        f'{title_text} exported for {date_range_str}',
        date_range=date_range_str,
        details={'report_type': filename_prefix, 'from_date': from_date.strftime('%Y-%m-%d'), 'to_date': to_date.strftime('%Y-%m-%d')}
    )
    
    response = send_file(tmp_filename, as_attachment=True, download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

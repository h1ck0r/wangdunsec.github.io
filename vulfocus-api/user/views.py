from django.core.paginator import Paginator
from django.db.models import Sum
from django.contrib.auth.models import AnonymousUser
from django.http import JsonResponse,HttpResponse
from rest_framework import viewsets,mixins
from user.serializers import UserProfileSerializer, User, UserRegisterSerializer, UpdatePassSerializer, LoginSerializer, CommentSerializer
from user.serializers import SendEmailSerializer,ResetPasswordSerializer
from rest_framework.views import APIView
from django.contrib.auth import logout, login, authenticate
from user.permissions import IsOwner
from django.db.models import Q
from email.header import Header
from rest_framework.decorators import action
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from django.views.generic.base import View
from user.models import UserProfile,EmailCode, RegisterCode, Comment
from django.core.mail import send_mail, EmailMessage, EmailMultiAlternatives
from rest_framework import permissions, status
from vulfocus.settings import EMAIL_FROM, EMAIL_HOST, EMAIL_HOST_USER
from dockerapi.common import R, get_setting_config
from dockerapi.models import ContainerVul
from vulfocus.settings import REDIS_IMG as r_img
from PIL import ImageDraw,ImageFont,Image
import random
import io
import datetime
from user.utils import generate_code, validate_email
import smtplib
import os
from email.mime.text import MIMEText
from time import sleep
import time
import uuid
import json
from rest_framework_jwt.serializers import JSONWebTokenSerializer
from rest_framework_jwt.utils import jwt_response_payload_handler
from rest_framework.response import Response
from datetime import datetime, timedelta
from rest_framework_jwt.settings import api_settings
from rest_framework.views import View
from dockerapi.views import get_local_ip
from captcha.models import CaptchaStore
from captcha.helpers import captcha_image_url
from vulfocus.settings import REDIS_USER_CACHE as red_user_cache
from vulfocus.settings import ALLOWED_IMG_SUFFIX, BASE_DIR
from dockerapi.views import get_local_ip, get_request_ip
from vulfocus.settings import EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD
import django.utils.timezone as timezone

class ListAndUpdateViewSet(mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    A viewset that provides default `update()`, `list()`actions.
    """
    pass


class UserSet(ListAndUpdateViewSet):
    serializer_class = UserProfileSerializer

    def get_queryset(self):
        if self.request.user.is_superuser:
            user_info = UserProfile.objects.all()
            query = self.request.GET.get('query', '')
            if query:
                user_info = UserProfile.objects.filter(Q(username__contains=query) | Q(email__contains=query)).all()
            return user_info

    def update(self, request, *args, **kwargs):
            user = request.user
            if not user.is_superuser:
                return JsonResponse(R.build(msg="????????????"))
            new_pwd = request.data.get("pwd", "")
            new_pwd = new_pwd.strip()
            if len(new_pwd) < 6:
                return JsonResponse(R.build(msg="?????????????????????"))
            user_info = self.get_object()
            user_info.set_password(new_pwd)
            user_info.save()
            return JsonResponse(R.ok())


class get_user_rank(APIView):

    def get(self, request):
        page_no = int(request.GET.get("page", 1))
        score_list = ContainerVul.objects.filter(is_check=True, time_model_id='').values('image_id').distinct().values('user_id').annotate(
            score=Sum("image_id__rank")).values('user_id', 'score').order_by("-score")
        len_score_list = [item for item in score_list if item['score'] != 0]
        try:
            pages = Paginator([item for item in score_list if item['score'] != 0], 20)
            page = pages.page(page_no)
        except Exception as e:
            return JsonResponse(R.err())
        result = []
        for _data in list(page):
            user_info = UserProfile.objects.filter(id=_data["user_id"]).first()
            username = ""
            pass_container_vuls = ""
            user_avatar = ""
            if user_info:
                username = user_info.username
                user_avatar = user_info.avatar
                pass_container_vuls = ContainerVul.objects.filter(is_check=True, user_id=user_info.id, time_model_id='').values('image_id').distinct().count()
            result.append({"rank": _data["score"], "name": username, "image_url": user_avatar, "pass_container_count": pass_container_vuls})

        data = {
            'results': result,
            'count': len(len_score_list)
        }
        return JsonResponse(R.ok(data=data))


class get_user_info(APIView):
    def get(self, request):
        user_info = User.objects.get(pk=request.user.id)
        serializer = UserProfileSerializer(user_info)
        return JsonResponse(serializer.data)


class LogoutView(View):
    def get(self, request):
        logout(request)
        return JsonResponse({"msg": "OK"})


class UserRegView(viewsets.mixins.CreateModelMixin, viewsets.GenericViewSet):
    authentication_classes = []
    permission_classes = []
    queryset = UserProfile.objects.all()
    serializer_class = UserRegisterSerializer

    def create(self, request, *args, **kwargs):
        username = request.data.get("username", "")
        password = request.data.get("password", "")
        checkpass = request.data.get("checkpass", "")
        email = request.data.get("email", "")
        captcha_code = request.data.get("captcha_code", "")
        hashkey = request.data.get("hashkey", "")
        get_setting_info = get_setting_config()
        if not username:
            return JsonResponse({"code": 400, "msg": "?????????????????????"})
        if UserProfile.objects.filter(username=username).count():
            return JsonResponse({"code": 400, "msg": "?????????????????????"})
        if not email:
            return JsonResponse({"code": 400, "msg": "??????????????????"})
        if UserProfile.objects.filter(email=email, has_active=True).count():
            return JsonResponse({"code": 400, "msg": "?????????????????????"})
        if not captcha_code:
            return JsonResponse({"code": 400, "msg": "?????????????????????"})
        if not judge_captcha(captcha_code, hashkey):
            return JsonResponse({"code": 400, "msg": "???????????????"})
        if password != checkpass:
            return JsonResponse({"code": 400, "msg": "???????????????????????????"})
        code = generate_code(6)
        keys = red_user_cache.keys()
        for single_key in keys:
            try:
                single_user_info = red_user_cache.get(single_key)
                redis_username, redis_password, redis_email = json.loads(single_user_info)
                if username == redis_username:
                    return JsonResponse({"code": 400, "msg": "?????????????????????"})
                if redis_email == email:
                    return JsonResponse({"code": 400, "msg": "?????????????????????"})
            except Exception as e:
                return JsonResponse({"code": 400, "msg": "??????????????????"})
        if get_setting_info['cancel_validation'] == False:
            user = UserProfile(username=username, email=email)
            user.set_password(password)
            user.has_active = True
            user.greenhand = True
            user.save()
            return JsonResponse({"code": 200, "msg": "????????????"})
        try:
            send_activate_email(receiver_email=email, code=code, request=request)
        except smtplib.SMTPDataError as e:
            return JsonResponse({"code": 400, "msg": "??????????????????????????????????????????"})
        except Exception as e:
            return JsonResponse({"code": 400, "msg": "??????????????????"})
        try:
            user_info = [username, password, email]
            red_user_cache.set(code, json.dumps(user_info), ex=300)
        except Exception as e:
            return JsonResponse({"code": 400, "msg": "????????????"})
        return JsonResponse({"code": 200, "msg": "???????????????????????????????????????????????????"})

# ??????????????????
class MyCode(View):

    # ??????????????????????????????
    def get_random_color(self):
        R = random.randrange(255)
        G = random.randrange(255)
        B = random.randrange(255)
        return (R,G,B)
    # ???????????????
    def get(self,request):
        img_size = (110,50)
        image = Image.new('RGB',img_size,'#27408B')
        draw = ImageDraw.Draw(image,'RGB')
        source = '0123456789abcdefghijklmnopqrstevwxyz'
        code_str = ''
        for i in range(4):
            text_color = self.get_random_color()
            tmp_num = random.randrange(len(source))
            random_str = source[tmp_num]
            code_str +=random_str
            draw.text((10+30*i,20),random_str,text_color,)
        buf = io.BytesIO()
        image.save(buf, 'png')
        data = buf.getvalue()
        if "HTTP_X_REAL_IP" in request.META:
            ip = request.META.get("HTTP_X_REAL_IP")
        else:
            ip = request.META.get("REMOTE_ADDR")
        if ip == "127.0.0.1":
            return HttpResponse(data, 'image/png')
        r_img.sadd(ip, code_str)
        r_img.expire(ip, 60)
        return HttpResponse(data, 'image/png')


class UpdatePassViewset(mixins.UpdateModelMixin,viewsets.GenericViewSet):
    serializer_class = UpdatePassSerializer
    permission_classes = [permissions.IsAuthenticated,IsOwner]

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):

        oldPassword = request.data['oldPassword'].strip()
        password = request.data["pass"].strip()
        checkPassword = request.data["checkPass"].strip()
        if not oldPassword:
            return JsonResponse({"code": 401, "msg": "?????????????????????"})
        if len(checkPassword) < 8:
            return JsonResponse({"code": 401, "msg": "??????????????????8???"})
        if password != checkPassword:
            return JsonResponse({"code": 401, "msg": "?????????????????????"})
        user = self.request.user
        if not user.check_password(oldPassword):
            return JsonResponse({"code": 401, "msg": "???????????????"})
        user.set_password(raw_password=checkPassword)
        user.save()
        return JsonResponse({"code": 200, "msg": "??????????????????"})


#???????????????
class LoginViewset(mixins.CreateModelMixin,viewsets.GenericViewSet):
    serializer_class = LoginSerializer
    queryset = User.objects.all()
    permission_classes = []
    authentication_classes = ()
    def create(self, request, *args, **kwargs):
        username = request.data["username"]
        password = request.data["password"]
        keys = red_user_cache.keys()
        try:
            for single_key in keys:
                user_info = red_user_cache.get(single_key)
                redis_username, redis_password, redis_email = json.loads(user_info)
                if redis_username == username:
                    return Response({"non_field_errors": ["????????????????????????????????????"]}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            pass
        user = User.objects.filter(username=username).first()
        if not user:
            return Response({"non_field_errors": ["????????????????????????"]}, status=status.HTTP_400_BAD_REQUEST)
        if not user.check_password(password):
            return Response({"non_field_errors": ["????????????????????????"]}, status=status.HTTP_400_BAD_REQUEST)
        if not user.has_active:
            return Response({"non_field_errors": ["????????????????????????????????????"]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        #??????jwt????????????
        serializer_instance = JSONWebTokenSerializer(data=request.data)
        if serializer_instance.is_valid():
            user = serializer_instance.object.get('user') or request.user
            token = serializer_instance.object.get('token')
            response_data = jwt_response_payload_handler(token, user, request)
            response = Response(response_data)
            if api_settings.JWT_AUTH_COOKIE:
                expiration = (datetime.utcnow() +
                              api_settings.JWT_EXPIRATION_DELTA)
                response.set_cookie(api_settings.JWT_AUTH_COOKIE,
                                    token,
                                    expires=expiration,
                                    httponly=True)
            return response
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class SendEmailViewset(mixins.CreateModelMixin,viewsets.GenericViewSet):
    serializer_class = SendEmailSerializer
    permission_classes = []

    def create(self, request, *args, **kwargs):
        http_referer = request.META.get('HTTP_REFERER')
        serializer = self.get_serializer(data=request.data)
        username = request.data.get("username", None)
        hashkey = request.data.get("hashkey", "")
        captcha_code = request.data.get("captcha_code", "")
        if not hashkey:
            return JsonResponse({"code": 400, "msg": "??????????????????????????????"})
        if not captcha_code:
            return JsonResponse({"code": 400, "msg": "?????????????????????"})
        if not judge_captcha(captcha_code, hashkey):
            return JsonResponse({"code": 400, "msg": "?????????????????????"})
        if not User.objects.filter(username=username).count():
            return JsonResponse({"code": 400, "msg": "??????????????????"})
        user = User.objects.get(username=username)
        five_minute_ago = datetime.now() - timedelta(minutes=5)
        if EmailCode.objects.filter(user=user, add_time__gt=five_minute_ago).count():
            return JsonResponse({"code": 400, "msg": "????????????????????????????????????"})
        code = generate_code()
        #???????????????????????????????????????????????????
        while EmailCode.objects.filter(code=code).count():
            code = generate_code()
        email_instance = EmailCode(user=user, code=code, email=user.email)
        try:
            send_update_password_email(receiver_email=user.email, code=code, request=request)
        except:
            return JsonResponse({"code": 400, "msg": "??????????????????"})
        email_instance.save()
        return JsonResponse({"code": 200, "msg": "ok"})



#????????????
class ResetPasswordViewset(mixins.UpdateModelMixin,viewsets.GenericViewSet):
    serializer_class = ResetPasswordSerializer
    permission_classes = []


    def update(self, request, *args, **kwargs):
        code = request.data.get("code", "")
        password = request.data.get("pass", "")
        check_password = request.data.get("checkPass", "")
        if not code:
            return JsonResponse({"code": 400, "msg": "???????????????"})
        five_minutes_ago = datetime.now() - timedelta(minutes=5)
        email_instance = EmailCode.objects.filter(code=code).first()
        if not email_instance:
            return JsonResponse({"code": 400, "msg": "????????????"})
        if email_instance.add_time <= five_minutes_ago:
            return JsonResponse({"code": 400, "msg": "??????????????????"})
        password = request.data['pass']
        if not password or not check_password:
            return JsonResponse({"code": 400, "msg": "??????????????????"})
        if len(password) < 8:
            return JsonResponse({"code": 400, "msg": "????????????????????????8???"})
        if password != check_password:
            return JsonResponse({"code": 400, "msg": "???????????????????????????"})
        user = email_instance.user
        user.set_password(password)
        user.save()
        email_instances = EmailCode.objects.filter(code=code).all()
        for email_i in email_instances:
            email_i.delete()
        return JsonResponse({"code": 200, "msg": "??????????????????"})


class AccessLinkView(View):
    def get(self,request):
        '''
        ????????????????????????
        '''
        code=request.GET.get("code","")
        try:
            user_info = red_user_cache.get(code)
            redis_username, redis_password, redis_email = json.loads(user_info)
            user = UserProfile(username=redis_username, email=redis_email)
            user.set_password(redis_password)
            user.has_active = True
            user.greenhand = True
            user.save()
            red_user_cache.delete(code)
        except Exception as e:
            return JsonResponse({"code": 400, "msg": "???????????????????????????"})
        return JsonResponse({"code": 200, "msg": "ok"})

@api_view(http_method_names=["POST"])
@authentication_classes([])
@permission_classes([])
def send_register_email(request):
    email = request.POST.get("email", "")
    code = generate_code(6)
    if not email:
        return JsonResponse({"code": 400, "msg": "??????????????????"})
    if UserProfile.objects.filter(email=email).count():
        return JsonResponse({"code": 400, "msg": "????????????????????????"})
    if RegisterCode.objects.filter(email=email, add_time__gt=datetime.now()-timedelta(minutes=1)).count():
        return JsonResponse({"code": 400, "msg": "???????????????????????????1??????"})
    try:
        send_mail(subject="????????????", from_email=EMAIL_FROM, message="??????????????????{}????????????????????????".format(code),
                  recipient_list=[email])
        register_code = RegisterCode(email=email, code=code)
        register_code.save()
        return JsonResponse({"code": 200, "msg": "??????????????????"})
    except Exception as e:
        return JsonResponse({"code": 400, "msg": "??????????????????"})


# ???????????????
def captcha():
    hashkey = CaptchaStore.generate_key()
    image_url = captcha_image_url(hashkey)
    captcha_code = {"hashkey": hashkey, "image_url": image_url}
    return captcha_code


# ???????????????????????????
def judge_captcha(captchastr, captchahashkey):
    if captchastr and captchahashkey:
        try:
            captcha_instance = CaptchaStore.objects.get(hashkey=captchahashkey)
            if captcha_instance.challenge == captchastr.upper():
                captcha_instance.delete()
                return True
        except Exception as e:
            return False
    else:
        return False


# ???????????????
@api_view(http_method_names=["GET"])
@authentication_classes([])
@permission_classes([])
def refresh_captcha(request):
    return JsonResponse(captcha())

def send_activate_email(receiver_email, code, request):
    subject, from_email, to = "????????????", EMAIL_FROM, receiver_email
    http_referer = request.META.get('HTTP_REFERER')
    msg = EmailMultiAlternatives(subject, '', from_email, [to])
    html_content ="""<div><table cellpadding="0" align="center" width="600" style="background:#fff;width:600px;margin:0 auto;text-align:left;position:relative;font-size:14px; font-family:'lucida Grande',Verdana;line-height:1.5;box-shadow:0 0 5px #999999;border-collapse:collapse;">
    <tbody><tr><th valign="middle" style="height:12px;color:#fff; font-size:14px;font-weight:bold;text-align:left;border-bottom:1px solid #467ec3;background:#2196f3;">
    </th></tr><tr><td><div style="padding:30px  40px;"><img style="float:left;" src="http://www.baimaohui.net/home/image/icon-anquan-logo.png?imageView2">
    <br><br><br><br><h2 style="font-weight:bold; font-size:14px;margin:5px 0;font-family:PingFang-SC-Regular">?????????</h2>
    <p style="color:#31424e;line-height:28px;font-size:14px;margin:20px 0;text-indent:2em;">???????????????vulfocus?????????5??????????????????????????????????????????????????????</p>
    <a href="{http_referer}#/activate?code={code}" style="color: #e21c23;text-decoration: underline;text-decoration: none;">
    <div style="height: 36px;line-height:36px;width:160px;border-radius:2px;margin:0 auto;margin-top: 30px;font-size: 16px;background:#2196f3;text-align: center;color: #FFF;">????????????</div></a>
    <p style="color:#31424e;line-height:28px;font-size:14px;margin:20px 0;text-indent:2em;">????????????????????????????????????????????????????????????????????????</p>
    <p style="color:#2196f3;line-height:28px;font-size:14px;margin:20px 0;text-indent:2em;">{http_referer}#/activate?code={code}</p>
    </div><div style="background: #f1f1f1;padding: 30px 40px;"><p style="color:#798d99; font-size:12px;padding: 0;margin: 0;">
    Vulfocus ???????????????<a href="http://vulfocus.fofa.so/#/" target="_blank" style="color:#999;text-decoration: none;">http://vulfocus.fofa.so/#/</a><br>
    <span style="background:#ddd;height:1px;width:100%;overflow:hidden;display:block;margin:8px 0;"></span>
    Vulfocus ????????????????????????????????????????????? docker ?????????????????????????????????????????????<br></p><div class="cons_list" style="text-align: center; margin: 48px 0;">
    <a href="http://vulfocus.fofa.so/#/" style="text-decoration: none;"><img src="http://www.baimaohui.net/home/image/icon-anquan-logo.png" style="width:42px; height:42px; display: inline-block;">
    <p style="width:100%;text-align: center;margin: 20px 0 0 0;"><a href="http://vulfocus.fofa.so/#/" style="border-right: 1px solid #ccc;  font-size:14px;margin: 0; font-weight:500; color:rgba(180,189,194,1); padding: 0 10px;text-decoration: none;">vulfocus??????</a>
    </p></div></div></td></tr></tbody></table>
    </div>""" .format(http_referer=http_referer, code=code)
    msg.attach_alternative(html_content, "text/html")
    msg.send()


class AccessUpdataLinkView(View):
    def get(self,request):
        '''
        ????????????????????????
        '''
        code=request.GET.get("code","")
        if not EmailCode.objects.filter(code=code).count():
            return JsonResponse({"code": 400, "msg": "???????????????????????????"})
        email_instance = EmailCode.objects.get(code=code)
        user = email_instance.user
        five_minutes_ago = datetime.now() - timedelta(minutes=5)
        if email_instance.add_time <= five_minutes_ago:
            return JsonResponse({"code": 400, "msg": "???????????????"})
        return JsonResponse({"code": 200, "msg": "ok"})


class CommentView(viewsets.ModelViewSet):
    '''
    ??????????????????
    '''
    serializer_class = CommentSerializer

    def get_queryset(self):
        scene_id = self.request.GET.get('sceneId', "")
        if not scene_id:
            return JsonResponse({"status": 201, "message": "???????????????"})
        scene_info = Comment.objects.filter(scene_id=scene_id).order_by('-create_time').all()
        return scene_info

    def create(self, request, *args, **kwargs):
        data = request.data
        user = request.user
        scene_id = data['scene_id']
        content = data['content']
        scene_type = data['scene_type']
        if not content:
            return JsonResponse({"status": 201, "message": "????????????????????????"})
        if not scene_id:
            return JsonResponse({"status": 201, "message": "???????????????"})
        if not scene_type:
            return JsonResponse({"status": 201, "message": "????????????????????????"})
        comment_info = Comment(comment_id=str(uuid.uuid4()))
        comment_info.scene_id = scene_id
        comment_info.user = user
        comment_info.content = content
        comment_info.scene_type = scene_type
        comment_info.create_time = timezone.now()
        comment_info.save()
        return JsonResponse({"status": 200, "message": '????????????'})

    @action(methods=["get"], detail=True, url_path="delete")
    def del_comment(self, request, pk=None):
        user = request.user
        if not pk:
            return JsonResponse(R.build(msg="??????????????????"))
        comment_info = Comment.objects.filter(comment_id=pk).first()
        if not comment_info:
            return JsonResponse(R.build(msg="???????????????"))
        if not user.is_superuser and comment_info.user != user:
            return JsonResponse(R.build(msg="????????????"))
        comment_info.delete()
        return JsonResponse(R.ok())



@api_view(http_method_names=["POST"])
def upload_user_img(request):
    user = request.user
    img = request.data.get("img")
    if not img:
        return JsonResponse({"code": 400, "msg": "???????????????"})
    img_name = img.name
    img_suffix = img_name.split(".")[-1]
    if img_suffix not in ALLOWED_IMG_SUFFIX:
        return JsonResponse({"code": 400, "msg": "????????????????????????????????????%s????????????" % ("???".join(ALLOWED_IMG_SUFFIX))})
    img_name = str(uuid.uuid4()).replace("-", "")+"."+img_suffix
    static_path = os.path.join(BASE_DIR, "static", "user")
    if not os.path.exists(static_path):
        os.mkdir(static_path)
    #  ?????????????????????????????????
    try:
        if user.avatar != "/images/user/bmh.png":
            origin_img_path = user.avatar.split("user")[-1]
            os.remove(static_path + origin_img_path)
    except Exception as e:
        pass
    with open(os.path.join(static_path, img_name), "wb") as f:
        for chunk in img.chunks():
            f.write(chunk)
    user.avatar = '/images/user/' + img_name
    user.save()
    return JsonResponse({"code": 200, "msg": "????????????", "image_path": img_name})


def send_update_password_email(receiver_email, code, request):
    subject, from_email, to = "????????????", EMAIL_FROM, receiver_email
    http_referer = request.META.get('HTTP_REFERER')
    msg = EmailMultiAlternatives(subject, '', from_email, [to])
    html_content ="""<div><table cellpadding="0" align="center" width="600" style="background:#fff;width:600px;margin:0 auto;text-align:left;position:relative;font-size:14px; font-family:'lucida Grande',Verdana;line-height:1.5;box-shadow:0 0 5px #999999;border-collapse:collapse;">
    <tbody><tr><th valign="middle" style="height:12px;color:#fff; font-size:14px;font-weight:bold;text-align:left;border-bottom:1px solid #467ec3;background:#2196f3;">
    </th></tr><tr><td><div style="padding:30px  40px;"><img style="float:left;" src="http://www.baimaohui.net/home/image/icon-anquan-logo.png?imageView2">
    <br><br><br><br><h2 style="font-weight:bold; font-size:14px;margin:5px 0;font-family:PingFang-SC-Regular">?????????</h2>
    <p style="color:#31424e;line-height:28px;font-size:14px;margin:20px 0;text-indent:2em;">??????????????????????????????5??????????????????????????????????????????????????????</p>
    <a href="{http_referer}#/updatepwd?code={code}" style="color: #e21c23;text-decoration: underline;text-decoration: none;">
    <div style="height: 36px;line-height:36px;width:160px;border-radius:2px;margin:0 auto;margin-top: 30px;font-size: 16px;background:#2196f3;text-align: center;color: #FFF;">????????????</div></a>
    <p style="color:#31424e;line-height:28px;font-size:14px;margin:20px 0;text-indent:2em;">????????????????????????????????????????????????????????????????????????</p>
    <p style="color:#2196f3;line-height:28px;font-size:14px;margin:20px 0;text-indent:2em;">{http_referer}#/updatepwd?code={code}</p>
    </div><div style="background: #f1f1f1;padding: 30px 40px;"><p style="color:#798d99; font-size:12px;padding: 0;margin: 0;">
    Vulfocus ???????????????<a href="http://vulfocus.fofa.so/#/" target="_blank" style="color:#999;text-decoration: none;">http://vulfocus.fofa.so/#/</a><br>
    <span style="background:#ddd;height:1px;width:100%;overflow:hidden;display:block;margin:8px 0;"></span>
    Vulfocus ????????????????????????????????????????????? docker ?????????????????????????????????????????????<br></p><div class="cons_list" style="text-align: center; margin: 48px 0;">
    <a href="http://vulfocus.fofa.so/#/" style="text-decoration: none;"><img src="http://www.baimaohui.net/home/image/icon-anquan-logo.png" style="width:42px; height:42px; display: inline-block;">
    <p style="width:100%;text-align: center;margin: 20px 0 0 0;"><a href="http://vulfocus.fofa.so/#/" style="border-right: 1px solid #ccc;  font-size:14px;margin: 0; font-weight:500; color:rgba(180,189,194,1); padding: 0 10px;text-decoration: none;">vulfocus??????</a>
    </p></div></div></td></tr></tbody></table>
    </div>""" .format(http_referer=http_referer, code=code)
    msg.attach_alternative(html_content, "text/html")
    msg.send()
